"""Integration test for the COA preflight gate wiring (M2).

Drives `_check_coa_gate` through a fake Spark that answers DESCRIBE + the
multi-COA and Tier-B aggregate probes, against the shipped pack's dim_account
node. Proves: multi-COA fails closed without acceptance; bronze-column-name
contract (probes use CodeCombinationChartOfAccountsId, never the silver alias).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from oracle_ai_data_platform_fusion_bundle.orchestrator.coa_gate import (
    AIDPF_2018_MULTI_COA_UNCONFIGURED,
)
from oracle_ai_data_platform_fusion_bundle.orchestrator.content_pack import load_pack
from oracle_ai_data_platform_fusion_bundle.orchestrator.node_preflight import (
    _check_coa_gate,
)

PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "oracle_ai_data_platform_fusion_bundle"
    / "content_packs"
    / "fusion-finance-starter"
)

GL_COA_COLUMNS = [
    "CodeCombinationCodeCombinationId",
    "CodeCombinationChartOfAccountsId",
    "CodeCombinationSegment1",
    "CodeCombinationSegment2",
    "CodeCombinationSegment3",
    "CodeCombinationSegment4",
    "CodeCombinationSegment5",
    "CodeCombinationSegment6",
    "CodeCombinationAccountType",
    "CodeCombinationEnabledFlag",
]


def _fake_spark(chart_rows: dict[str, int], na_ambiguous: int = 0, na_total: int = 500):
    """Fake Spark: DESCRIBE returns gl_coa columns; the multi-COA GROUP BY
    returns chart_rows; the Tier-B aggregate returns (total, ambiguous)."""
    spark = MagicMock()

    def _sql(query: str):
        df = MagicMock()
        q = " ".join(query.split())
        if q.startswith("DESCRIBE TABLE"):
            df.collect.return_value = [(c, "string", None) for c in GL_COA_COLUMNS]
        elif "GROUP BY CAST(CodeCombinationChartOfAccountsId AS STRING)" in q:
            assert "chart_of_accounts_id" not in q.replace(
                "AS chart_id", ""
            ), "probe must use the bronze column, not the silver alias"
            df.collect.return_value = [(cid, n) for cid, n in chart_rows.items()]
        else:  # Tier-B natural-account aggregate
            df.collect.return_value = [(na_total, na_ambiguous)]
        return df

    spark.sql.side_effect = _sql
    return spark


def _ctx():
    ctx = MagicMock()
    ctx.bronze_table_for_source = {"gl_coa": "cat.bronze.gl_coa"}
    return ctx


def _profile(coa: dict):
    prof = MagicMock()
    prof.profile = {"chartOfAccounts": coa}
    return prof


SINGLETON_COA = {
    "default": {
        "balancingSegment": "CodeCombinationSegment1",
        "costCenterSegment": "CodeCombinationSegment2",
        "naturalAccountSegment": "CodeCombinationSegment3",
    }
}


def _dim_account_node(pack):
    return pack.silver["dim_account"]


def test_single_coa_singleton_passes() -> None:
    pack = load_pack(PACK_ROOT)
    spark = _fake_spark({"101": 15000})
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(SINGLETON_COA), _ctx()
    )
    assert errs == [], [e.message for e in errs]


def test_multi_coa_singleton_fails_closed() -> None:
    pack = load_pack(PACK_ROOT)
    spark = _fake_spark({"101": 15000, "5023": 8000})
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(SINGLETON_COA), _ctx()
    )
    assert AIDPF_2018_MULTI_COA_UNCONFIGURED in {e.code for e in errs}


def test_multi_coa_with_singleton_accepted_passes() -> None:
    pack = load_pack(PACK_ROOT)
    coa = dict(SINGLETON_COA)
    coa["singletonAccepted"] = True
    spark = _fake_spark({"101": 15000, "5023": 8000})
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(coa), _ctx()
    )
    assert errs == []


# --- M3: $coa.* union existence + byChart completeness ----------------------

from oracle_ai_data_platform_fusion_bundle.orchestrator.node_preflight import (  # noqa: E402
    preflight_node,
)

BYCHART_COA = {
    "default": SINGLETON_COA["default"],
    "byChart": {
        "101": SINGLETON_COA["default"],
        "5023": {
            "balancingSegment": "CodeCombinationSegment4",
            "costCenterSegment": "CodeCombinationSegment2",
            "naturalAccountSegment": "CodeCombinationSegment5",
        },
    },
}


def test_required_columns_union_missing_arm_column_blocks_preflight() -> None:
    """A byChart arm referencing Segment4, absent from landed gl_coa, blocks
    preflight via the $coa.* union (M3 required addition #1)."""
    pack = load_pack(PACK_ROOT)
    # gl_coa fixture WITHOUT Segment4/5 (arm 5023's columns).
    cols = [c for c in GL_COA_COLUMNS if c not in ("CodeCombinationSegment4", "CodeCombinationSegment5")]
    spark = MagicMock()

    def _sql(query: str):
        df = MagicMock()
        q = " ".join(query.split())
        if q.startswith("DESCRIBE TABLE"):
            df.collect.return_value = [(c, "string", None) for c in cols]
        elif "GROUP BY CAST(CodeCombinationChartOfAccountsId AS STRING)" in q:
            df.collect.return_value = [("101", 15000), ("5023", 8000)]
        else:
            df.collect.return_value = [(500, 0)]
        return df

    spark.sql.side_effect = _sql
    report = preflight_node(
        spark, _dim_account_node(pack), pack, _profile(BYCHART_COA), _ctx()
    )
    assert not report.ok
    assert any(e.code == "AIDPF-2042" for e in report.errors)


def test_byChart_completeness_unmapped_chart_fails() -> None:
    """A present active chart with no byChart arm fails closed (L3.4)."""
    pack = load_pack(PACK_ROOT)
    # byChart maps 101 + 5023, but live gl_coa also has unmapped chart 999.
    spark = _fake_spark({"101": 15000, "5023": 8000, "999": 4000})
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(BYCHART_COA), _ctx()
    )
    assert any(e.code == "AIDPF-2018" for e in errs)
    assert any("999" in e.message for e in errs)


def test_malicious_coa_column_blocks_before_tier_b_probe() -> None:
    """A hand-edited tenant profile with an injection/invalid naturalAccountSegment
    must fail the identifier allowlist BEFORE any Tier B probe SQL is built."""
    pack = load_pack(PACK_ROOT)
    sql_calls: list[str] = []
    spark = MagicMock()

    def _sql(query: str):
        sql_calls.append(query)
        df = MagicMock()
        df.collect.return_value = [(c, "string", None) for c in GL_COA_COLUMNS]
        return df

    spark.sql.side_effect = _sql
    bad = {
        "default": {
            "balancingSegment": "CodeCombinationSegment1",
            "costCenterSegment": "CodeCombinationSegment2",
            "naturalAccountSegment": "CodeCombinationSegment3) FROM x; DROP TABLE y--",
        }
    }
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(bad), _ctx()
    )
    assert any(e.code == "AIDPF-5001" for e in errs), [e.code for e in errs]
    # No Tier B aggregate (the GROUP BY <na_col> probe) was ever constructed.
    assert not any("GROUP BY" in q and "DROP TABLE" in q for q in sql_calls)
    assert not any("DROP TABLE" in q for q in sql_calls)


def test_byChart_covering_all_active_charts_passes() -> None:
    """Remediation end state: byChart maps every active chart -> gate passes
    (the multi-COA block is resolved by authoring byChart, not --accept)."""
    pack = load_pack(PACK_ROOT)
    spark = _fake_spark({"101": 15000, "5023": 8000})
    errs = _check_coa_gate(
        spark, _dim_account_node(pack), pack, _profile(BYCHART_COA), _ctx()
    )
    assert errs == [], [e.message for e in errs]
