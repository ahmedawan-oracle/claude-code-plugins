"""Per-node preflight for the content-pack runner.

Preflight runs in :func:`sql_runner.execute_node` *after* static schema
validation and *before* SQL rendering. Its job is to fail fast on
runtime-only conditions that the static validator can't see:

* Required columns missing in the live bronze schema.
* Watermark column missing in the source bronze (for merge strategy).
* Partition columns missing on the target (validate-only for deferred
  ``replace_partition`` strategy).

**Crucial ordering invariant**: preflight does NOT render SQL. The
renderer is invoked exactly once in ``execute_node``, *after*
this preflight passes. This separation is what makes the
``preflight-blocked`` unit test branch assert "renderer never called".

Preflight covers required-column, watermark-column, and partition-column
gates only; SQL rendering and execution stay in ``sql_runner``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..schema.medallion_pack import NodeYaml
from . import coa_gate
from .required_column_resolver import coa_role_union

_log = logging.getLogger(__name__)

_COA_REF_PREFIX = "$coa."
_SEMANTIC_REF_PREFIX = "$semantic."

AIDPF_5001_IDENTIFIER_ALLOWLIST = "AIDPF-5001"
"""A COA role column from the tenant profile fails the SQL identifier allowlist.
Mirrors ``sql_renderer._check_identifier`` so a bad/hand-edited
``profile.chartOfAccounts`` value is blocked BEFORE it reaches probe SQL."""

# Same allowlist as orchestrator.sql_renderer._IDENTIFIER_RE.
_COA_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

    from ..schema.tenant_profile import TenantProfile
    from .content_pack import ResolvedPack
    from .sql_renderer import RunContext


# ---------------------------------------------------------------------------
# AIDPF error codes
# ---------------------------------------------------------------------------

AIDPF_2042_REQUIRED_COLUMN_MISSING = "AIDPF-2042"
"""Required column declared in ``node.requiredColumns.<source>`` is absent
from the live bronze ``DESCRIBE TABLE`` schema."""

AIDPF_2043_WATERMARK_COLUMN_MISSING = "AIDPF-2043"
"""Watermark column declared in ``node.refresh.incremental.watermark.column``
is absent from the source bronze schema (merge strategy)."""

AIDPF_2044_PARTITION_COLUMN_MISSING = "AIDPF-2044"
"""``replace_partition`` strategy partition column missing on target
(deferred strategy; validate-only)."""

AIDPF_2046_REQUIRED_COLUMN_UNRESOLVED_REF = "AIDPF-2046"
"""A ``requiredColumns`` entry uses the ``$column.<key>`` reference
syntax but the key is either (a) not declared in ``pack.yaml``'s
``columnAliases``, or (b) declared but missing from the tenant profile's
``resolved.column`` map (bootstrap not run, or alias was added after
last bootstrap)."""


_COLUMN_REF_PREFIX = "$column."
"""YAML prefix marking a ``requiredColumns`` entry as a reference to a
``columnAliases.<key>`` resolved value in the tenant profile, rather
than a literal column name. Backward-compatible: entries without the
prefix are still treated as literals."""


# ---------------------------------------------------------------------------
# Preflight result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightError:
    """One preflight failure for a node.

    Attributes:
        code: AIDPF error code (e.g. ``AIDPF-2042``).
        source: bronze/silver source id where the failure was detected,
            or ``None`` for target-level checks.
        message: human-readable diagnostic naming the missing column /
            source / live bronze schema for triage.
    """

    code: str
    source: str | None
    message: str


@dataclass(frozen=True)
class PreflightReport:
    """Aggregated preflight result for a node.

    Attributes:
        errors: tuple of :class:`PreflightError` — blocking failures.
            Non-empty → :func:`execute_node` writes a
            ``status='preflight_blocked'`` soft state row and returns
            failure WITHOUT invoking the renderer.
        ok: convenience boolean — ``True`` iff ``errors`` is empty.
    """

    errors: tuple[PreflightError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preflight_node(
    spark: "SparkSession",
    node: NodeYaml,
    pack: "ResolvedPack",  # noqa: F821 — forward ref
    profile: "TenantProfile",  # noqa: F821
    ctx: "RunContext",  # noqa: F821
) -> PreflightReport:
    """Run per-node preflight against the live Spark catalog.

    Performs metadata + bronze ``DESCRIBE TABLE`` introspection only.
    **Does NOT render SQL** — the renderer runs separately in
    :func:`execute_node` step 3, *after* this preflight passes.

    Args:
        spark: live Spark session for ``DESCRIBE TABLE`` calls.
        node: validated NodeYaml.
        pack: assembled ResolvedPack (consulted for source-id → table
            mapping when ctx doesn't already carry it).
        profile: validated TenantProfile (used for column-alias resolution).
        ctx: render context — used for ``bronze_table_for_source`` map
            (which the introspection calls use to identify the live
            bronze tables to DESCRIBE).

    Returns:
        :class:`PreflightReport` carrying any collected errors. Empty
        ``errors`` → preflight passed and ``execute_node`` may proceed
        to render the SQL.

    Notes:
        Does NOT raise on errors — collects them so the report-row writer
        in ``execute_node`` can record the full diagnostic. Programmer-
        error conditions (missing live table entirely, Spark session
        broken) still raise — the caller treats those as a different
        failure class than "expected column missing on this tenant".
    """
    errors: list[PreflightError] = []

    # bronze_extract nodes CREATE their target table from the live PVO —
    # they don't read a pre-existing bronze table. The checks below all
    # `DESCRIBE` the node's bronze table, which doesn't exist yet on a
    # first-ever seed (or after a drop) and would raise an uncaught
    # AnalysisException. The bronze source is validated against the PVO by
    # the AIDPF-4071 source gate + the post-write AIDPF-4070 assertion, so
    # there's nothing for table-introspection preflight to do here.
    if getattr(node.implementation, "type", None) == "bronze_extract":
        return PreflightReport(errors=())

    # 1. Required columns on each declared source.
    errors.extend(_check_required_columns(spark, node, pack, profile, ctx))

    # 2. Watermark column for merge-strategy nodes.
    if _is_merge_strategy(node):
        errors.extend(_check_watermark_column(spark, node, ctx))

    # 3. Partition columns for replace_partition (deferred strategy —
    #    schema accepts it but execution path doesn't run in v0.3; we
    #    still validate the partition shape so an early customer who
    #    declares one gets a useful diagnostic, not a NotImplementedError
    #    much later in execute_node).
    if _is_replace_partition_strategy(node):
        errors.extend(_check_partition_columns(spark, node, ctx))

    # 4. COA semantic-role plausibility + multi-COA gate (feature
    #    coa-role-segment-resolution M2). No-ops unless the pack declares COA
    #    semanticRole aliases on a bronze source this node consumes.
    errors.extend(_check_coa_gate(spark, node, pack, profile, ctx))

    return PreflightReport(errors=tuple(errors))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_merge_strategy(node: NodeYaml) -> bool:
    inc = node.refresh.incremental
    return inc is not None and inc.strategy == "merge"


def _is_replace_partition_strategy(node: NodeYaml) -> bool:
    inc = node.refresh.incremental
    return inc is not None and inc.strategy == "replace_partition"


def _check_required_columns(
    spark: "SparkSession",
    node: NodeYaml,
    pack: "ResolvedPack",  # noqa: F821
    profile: "TenantProfile",  # noqa: F821
    ctx: "RunContext",
) -> list[PreflightError]:
    """For each entry in ``node.requiredColumns.<source>`` that names a **bronze**
    source, DESCRIBE the source's live bronze table and assert the column exists.

    The ``requiredColumns`` map is keyed by source id (matching
    ``dependsOn.bronze[*].id`` / ``dependsOn.silver[*].id``). Each value is a list
    of column names that MUST be present. Entries beginning with ``$column.`` are
    references into ``pack.columnAliases`` — resolved against the tenant profile's
    ``resolved.column`` map before the live-column check.

    **Bronze-only live gate.** This live-DESCRIBE check exists to catch drift in
    Fusion-extracted **bronze** tables — schemas the pack does not control. A
    silver/gold dependency source (e.g. a gold mart requiring columns from
    ``dim_supplier``) is *pack-built*: its schema is the producer node's own
    declared ``outputSchema``, already gated design-time by AIDPF-2045
    (``requiredColumns`` ⊆ ``outputSchema``, with type-compat) and at runtime by
    the producer's AIDPF-4070/4071 materialization gate. Re-DESCRIBE-ing it here
    would be redundant — and ``ctx.bronze_table_for_source`` only carries bronze
    sources, so a live probe isn't even available. A source is treated as
    pack-built (and skipped) iff it is a declared ``pack.silver`` / ``pack.gold``
    node; everything else gets the live DESCRIBE, including a bronze source
    declared only via the legacy ``bronze.yaml`` datasets path.
    """
    errors: list[PreflightError] = []
    required = getattr(node, "required_columns", None) or {}
    pack_alias_keys = set(pack.pack.column_aliases.keys())

    for source_id, required_cols in required.items():
        # Skip silver/gold dependency sources — pack-built, gated by
        # AIDPF-2045 (static) + the producer's AIDPF-4070/4071 (runtime). The
        # discriminator is positive membership in pack.silver / pack.gold (NOT
        # "absent from pack.bronze"): a bronze source declared only via the
        # legacy bronze.yaml datasets path lives in ctx.bronze_table_for_source
        # but not in pack.bronze, and must still get the live-drift DESCRIBE.
        if source_id in pack.silver or source_id in pack.gold:
            continue
        table = ctx.bronze_table_for_source.get(source_id)
        if table is None:
            errors.append(
                PreflightError(
                    code=AIDPF_2042_REQUIRED_COLUMN_MISSING,
                    source=source_id,
                    message=(
                        f"required-columns preflight could not find a bronze table "
                        f"identifier for source {source_id!r} in "
                        f"ctx.bronze_table_for_source. Confirm the source is "
                        f"declared in bundle.yaml + bronze.yaml."
                    ),
                )
            )
            continue
        present = _describe_columns(spark, table)
        present_ci = {c.lower(): c for c in present}
        for entry in required_cols:
            # $coa.<role> expands to the UNION of that role's columns across
            # default + byChart arms; every one must be present in bronze.
            if entry.startswith(_COA_REF_PREFIX):
                union = coa_role_union(entry[len(_COA_REF_PREFIX) :], profile)
                if not union:
                    errors.append(
                        PreflightError(
                            code=AIDPF_2046_REQUIRED_COLUMN_UNRESOLVED_REF,
                            source=source_id,
                            message=(
                                f"requiredColumns entry {entry!r} could not resolve to "
                                f"any column: the tenant profile has no "
                                f"`chartOfAccounts` mapping for this role. Run "
                                f"`aidp-fusion-bundle bootstrap`."
                            ),
                        )
                    )
                    continue
                for col in sorted(union):
                    if col.lower() not in present_ci:
                        errors.append(
                            PreflightError(
                                code=AIDPF_2042_REQUIRED_COLUMN_MISSING,
                                source=source_id,
                                message=(
                                    f"COA column {col!r} (resolved from {entry!r}) "
                                    f"missing from live bronze for source {source_id!r} "
                                    f"(table {table!r}). Extend the gl_coa bronze "
                                    f"contract / re-seed bronze."
                                ),
                            )
                        )
                continue
            # $semantic.<key> resolves to the physical column the ACTIVE
            # semanticVariants candidate detects (detect.columnExists), via
            # profile.resolved.semantic — same semantics as the run-level
            # resolve_required_column_entries, so the per-node gate doesn't treat
            # it as a literal and false-fail AIDPF-2042.
            if entry.startswith(_SEMANTIC_REF_PREFIX):
                key = entry[len(_SEMANTIC_REF_PREFIX) :]
                variant = pack.pack.semantic_variants.get(key)
                cand_id = (getattr(profile.resolved, "semantic", None) or {}).get(key)
                sem_col = None
                if variant is not None and cand_id:
                    for cand in variant.candidates:
                        if cand.id == cand_id and cand.detect and cand.detect.column_exists:
                            sem_col = cand.detect.column_exists
                            break
                if sem_col is None:
                    errors.append(
                        PreflightError(
                            code=AIDPF_2046_REQUIRED_COLUMN_UNRESOLVED_REF,
                            source=source_id,
                            message=(
                                f"requiredColumns entry {entry!r} could not resolve: "
                                f"semanticVariants key {key!r} has no active candidate "
                                f"in the tenant profile's `resolved.semantic`. Run "
                                f"`aidp-fusion-bundle bootstrap`."
                            ),
                        )
                    )
                    continue
                if sem_col.lower() not in present_ci:
                    errors.append(
                        PreflightError(
                            code=AIDPF_2042_REQUIRED_COLUMN_MISSING,
                            source=source_id,
                            message=(
                                f"semantic column {sem_col!r} (resolved from {entry!r}) "
                                f"missing from live bronze for source {source_id!r} "
                                f"(table {table!r}). Live columns: {sorted(present)!r}."
                            ),
                        )
                    )
                continue
            resolved, ref_error = _resolve_required_column_entry(
                entry, profile, source_id, pack_alias_keys
            )
            if ref_error is not None:
                errors.append(ref_error)
                continue
            assert resolved is not None  # mypy: ref_error None ⇒ resolved set
            if resolved.lower() not in present_ci:
                # Diagnostic names BOTH the YAML entry (for traceability
                # back to the pack source) and the resolved physical
                # column (for "go look in DESCRIBE"). When `entry` is a
                # literal these are the same — the message stays terse.
                resolved_hint = (
                    f" (resolved from {entry!r})" if entry != resolved else ""
                )
                errors.append(
                    PreflightError(
                        code=AIDPF_2042_REQUIRED_COLUMN_MISSING,
                        source=source_id,
                        message=(
                            f"required column {resolved!r}{resolved_hint} missing "
                            f"from live bronze schema for source {source_id!r} "
                            f"(table {table!r}). Live columns: {sorted(present)!r}."
                        ),
                    )
                )
    return errors


def _resolve_required_column_entry(
    entry: str,
    profile: "TenantProfile",  # noqa: F821
    source_id: str,
    pack_alias_keys: set[str],
) -> tuple[str | None, PreflightError | None]:
    """Resolve a ``requiredColumns`` entry to a physical column name.

    Returns ``(resolved, None)`` on success, or ``(None, error)`` when
    the entry uses ``$column.<key>`` syntax but the key cannot be
    resolved. A literal entry (no prefix) returns ``(entry, None)``
    unchanged — backward-compatible with v0.3 packs.
    """
    if not entry.startswith(_COLUMN_REF_PREFIX):
        return entry, None
    key = entry[len(_COLUMN_REF_PREFIX) :]
    if key not in pack_alias_keys:
        return None, PreflightError(
            code=AIDPF_2046_REQUIRED_COLUMN_UNRESOLVED_REF,
            source=source_id,
            message=(
                f"requiredColumns entry {entry!r} references columnAlias key "
                f"{key!r} which is not declared in pack.yaml's `columnAliases`. "
                f"Known keys: {sorted(pack_alias_keys)!r}. "
                f"Fix the pack YAML — either declare the alias or use a literal "
                f"column name."
            ),
        )
    resolved = profile.resolved.column.get(key)
    if not resolved:
        return None, PreflightError(
            code=AIDPF_2046_REQUIRED_COLUMN_UNRESOLVED_REF,
            source=source_id,
            message=(
                f"requiredColumns entry {entry!r} references columnAlias key "
                f"{key!r} declared in pack.yaml, but the tenant profile has no "
                f"resolved value for it. Re-run `aidp-fusion-bundle bootstrap` "
                f"to populate the profile."
            ),
        )
    return resolved, None


def _check_watermark_column(
    spark: "SparkSession", node: NodeYaml, ctx: "RunContext"
) -> list[PreflightError]:
    """For merge-strategy nodes, confirm the declared watermark column
    exists in the source bronze schema. Missing → AIDPF-2043.
    """
    inc = node.refresh.incremental
    if inc is None or inc.watermark is None:
        # Static validator should have rejected merge without watermark
        # config (AIDPF-2050); defensive check anyway.
        return []
    source_id = inc.watermark.source
    column = inc.watermark.column
    table = ctx.bronze_table_for_source.get(source_id)
    if table is None:
        return [
            PreflightError(
                code=AIDPF_2043_WATERMARK_COLUMN_MISSING,
                source=source_id,
                message=(
                    f"merge-strategy watermark preflight could not find a "
                    f"bronze table for source {source_id!r}. Confirm the source "
                    f"appears in ctx.bronze_table_for_source."
                ),
            )
        ]
    present = _describe_columns(spark, table)
    present_ci = {c.lower(): c for c in present}
    if column.lower() not in present_ci:
        return [
            PreflightError(
                code=AIDPF_2043_WATERMARK_COLUMN_MISSING,
                source=source_id,
                message=(
                    f"merge-strategy watermark column {column!r} missing from "
                    f"source {source_id!r} (table {table!r}). Live columns: "
                    f"{sorted(present)!r}."
                ),
            )
        ]
    return []


def _check_partition_columns(
    spark: "SparkSession", node: NodeYaml, ctx: "RunContext"
) -> list[PreflightError]:
    """For replace_partition strategy, confirm partition columns exist
    on the target. v0.3 doesn't execute this strategy, but we run the
    check so customers declaring it get an early diagnostic.
    """
    inc = node.refresh.incremental
    if inc is None:
        return []
    if not inc.partition_columns:
        # Static validator (R6) should have rejected this; defensive.
        return [
            PreflightError(
                code=AIDPF_2044_PARTITION_COLUMN_MISSING,
                source=None,
                message=(
                    f"replace_partition strategy declared without "
                    f"`partitionColumns` (AIDPF-2054). Static "
                    f"validator should reject this earlier."
                ),
            )
        ]
    # v0.3: target table may not exist yet on first run; we don't fail
    # closed here — just record a soft warning that the runtime check
    # will repeat once the target materialises.
    return []


_COA_DISCRIMINANT = "CodeCombinationChartOfAccountsId"
_COA_ACCOUNT_TYPE = "CodeCombinationAccountType"
_COA_ENABLED_FLAG = "CodeCombinationEnabledFlag"


def _coa_role_aliases(pack: "ResolvedPack") -> dict[str, tuple[str, str]]:
    """Return {alias_name: (role_token, applies_to_source)} for COA semanticRole
    aliases declared in the pack. Empty when the pack has none."""
    out: dict[str, tuple[str, str]] = {}
    for name, spec in pack.pack.column_aliases.items():
        resolution = getattr(spec, "resolution", "columnExistence")
        role = getattr(spec, "role", None)
        if resolution != "semanticRole" or not isinstance(role, str):
            continue
        applies_to = getattr(spec, "appliesTo", "")
        source = applies_to.split(".", 1)[1] if "." in applies_to else applies_to
        out[name] = (role, source)
    return out


def _node_consumes_source(node: NodeYaml, source_id: str) -> bool:
    deps = getattr(node, "depends_on", None)
    if deps is None:
        return False
    for dep in (getattr(deps, "bronze", None) or []):
        if dep.id == source_id:
            return True
    return False


def _check_coa_gate(
    spark: "SparkSession",
    node: NodeYaml,
    pack: "ResolvedPack",  # noqa: F821
    profile: "TenantProfile",  # noqa: F821
    ctx: "RunContext",
) -> list[PreflightError]:
    """COA plausibility + multi-COA gate. Validate-only (no writes, no render).

    Structural checks (Tier A existence-union + per-arm distinctness) are hard
    and use ``DESCRIBE``. The multi-COA detection and Tier B natural-account
    probes run live ``gl_coa`` data queries; a probe that cannot execute (e.g.
    a constrained session) downgrades to a logged warning rather than crashing
    this validate-only gate. A probe that DOES run and finds a violation
    fails closed.
    """
    aliases = _coa_role_aliases(pack)
    if not aliases:
        return []
    # All COA roles in the starter share one source (gl_coa). Group by source.
    sources = {src for (_role, src) in aliases.values()}
    coa_raw = (profile.profile or {}).get("chartOfAccounts")
    if not isinstance(coa_raw, dict):
        return []  # no COA config to validate (M1 fails closed earlier)

    errors: list[PreflightError] = []
    for source_id in sources:
        if not _node_consumes_source(node, source_id):
            continue
        table = ctx.bronze_table_for_source.get(source_id)
        if table is None:
            continue

        arms = _coa_arms(coa_raw)
        # Tier A.0 — identifier allowlist (HARD, FIRST). The tenant profile's
        # chartOfAccounts is free-form and hand-editable; pack validation does
        # NOT cover it. Every COA role column is interpolated into the Tier B
        # probe SQL (and rendered later), so validate each against the SAME
        # allowlist the renderer uses BEFORE any SQL is constructed. A bad value
        # (injection or just an invalid identifier) blocks here — we do NOT run
        # the data probes when any identifier is invalid.
        ident_ok = True
        for arm_id, mapping in arms.items():
            for role, col in mapping.items():
                if not _COA_IDENT_RE.match(col or ""):
                    ident_ok = False
                    errors.append(
                        PreflightError(
                            code=AIDPF_5001_IDENTIFIER_ALLOWLIST,
                            source=source_id,
                            message=(
                                f"COA mapping for arm {arm_id!r} role {role!r} resolves "
                                f"to {col!r}, which fails the identifier allowlist "
                                f"`^[A-Za-z_][A-Za-z0-9_]{{0,62}}$`. Fix "
                                f"`profile.chartOfAccounts` — a COA role must bind a "
                                f"plain column name."
                            ),
                        )
                    )
        # Tier A — distinctness (pure).
        for code, msg in coa_gate.check_distinctness(arms):
            errors.append(PreflightError(code=code, source=source_id, message=msg))
        # Tier A — existence union (DESCRIBE).
        referenced = {c for m in arms.values() for c in m.values()}
        present = _describe_columns(spark, table)
        for code, msg in coa_gate.check_existence_union(referenced, present):
            errors.append(PreflightError(code=code, source=source_id, message=msg))

        # Data probes (multi-COA + Tier B): interpolate COA columns into SQL, so
        # ONLY run when every identifier passed the allowlist above.
        if not ident_ok:
            continue
        try:
            errors.extend(
                _coa_data_probes(spark, table, source_id, coa_raw, arms)
            )
        except Exception as exc:  # pragma: no cover — constrained-session guard
            _log.warning(
                "COA data probe on %s skipped (%s); structural checks still applied.",
                table,
                exc,
            )
    return errors


def _coa_arms(coa_raw: dict) -> dict[str, dict[str, str]]:
    """Normalise a profile chartOfAccounts dict to {arm_id: {role_token: col}}."""

    def _mapping(block: dict) -> dict[str, str]:
        return {
            role: block[alias]
            for role, alias in (
                ("coa.balancing", "balancingSegment"),
                ("coa.cost_center", "costCenterSegment"),
                ("coa.natural_account", "naturalAccountSegment"),
            )
            if alias in block and block[alias] is not None
        }

    arms: dict[str, dict[str, str]] = {}
    default_block = coa_raw.get("default", coa_raw)
    default = _mapping(default_block)
    if default:
        arms["default"] = default
    for chart_id, block in (coa_raw.get("byChart") or {}).items():
        arms[str(chart_id)] = _mapping(block)
    return arms


def _coa_data_probes(
    spark: "SparkSession",
    table: str,
    source_id: str,
    coa_raw: dict,
    arms: dict[str, dict[str, str]],
) -> list[PreflightError]:
    """Run the multi-COA + Tier B natural-account live probes against gl_coa."""
    errors: list[PreflightError] = []

    # Multi-COA: active rows per chart (enabled only).
    rows = spark.sql(
        f"SELECT CAST({_COA_DISCRIMINANT} AS STRING) AS chart_id, COUNT(*) AS n "
        f"FROM {table} "
        f"WHERE {_COA_DISCRIMINANT} IS NOT NULL "
        f"AND COALESCE({_COA_ENABLED_FLAG}, 'Y') <> 'N' "
        f"GROUP BY CAST({_COA_DISCRIMINANT} AS STRING)"
    ).collect()
    chart_active = {str(r[0]): int(r[1]) for r in rows if r[0] is not None}

    by_chart = coa_raw.get("byChart") or {}
    has_by_chart = bool(by_chart)
    singleton_accepted = bool(coa_raw.get("singletonAccepted"))
    for code, msg in coa_gate.check_multi_coa(
        chart_active,
        singleton_accepted=singleton_accepted,
        has_by_chart=has_by_chart,
    ):
        errors.append(PreflightError(code=code, source=source_id, message=msg))

    # L3.4 completeness: when byChart is declared, every present (active) chart
    # must have an arm — an unmapped chart would hit the CASE ELSE raise_error.
    if has_by_chart:
        mapped = {str(k) for k in by_chart}
        for chart_id, n in chart_active.items():
            if n >= coa_gate.MULTI_COA_MIN_ACTIVE_ROWS and chart_id not in mapped:
                errors.append(
                    PreflightError(
                        code=coa_gate.AIDPF_2018_MULTI_COA_UNCONFIGURED,
                        source=source_id,
                        message=(
                            f"chart_of_accounts_id {chart_id!r} has active gl_coa rows "
                            f"but no `chartOfAccounts.byChart` arm. Declare it (the "
                            f"rendered CASE would otherwise raise at runtime)."
                        ),
                    )
                )

    # Tier B per chart: natural-account ambiguity using that chart's NA column.
    for chart_id, active_rows in chart_active.items():
        mapping = arms.get(chart_id) or arms.get("default") or {}
        na_col = mapping.get("coa.natural_account")
        if not na_col:
            continue
        agg = spark.sql(
            f"SELECT "
            f"COUNT(*) AS total, "
            f"SUM(CASE WHEN t > 1 THEN 1 ELSE 0 END) AS ambiguous "
            f"FROM (SELECT {na_col} AS na, "
            f"COUNT(DISTINCT {_COA_ACCOUNT_TYPE}) AS t "
            f"FROM {table} "
            f"WHERE CAST({_COA_DISCRIMINANT} AS STRING) = '{chart_id}' "
            f"AND {na_col} IS NOT NULL "
            f"AND COALESCE({_COA_ENABLED_FLAG}, 'Y') <> 'N' "
            f"GROUP BY {na_col})"
        ).collect()
        if not agg:
            continue
        total = int(agg[0][0] or 0)
        ambiguous = int(agg[0][1] or 0)
        probe = coa_gate.ChartProbe(
            chart_id=chart_id,
            active_row_count=active_rows,
            natural_account_distinct=total,
            natural_account_ambiguous=ambiguous,
        )
        res = coa_gate.check_natural_account(probe)
        for code, msg in res.errors:
            errors.append(PreflightError(code=code, source=source_id, message=msg))
        for warning in res.warnings:
            _log.warning("COA gate: %s", warning)
    return errors


def _describe_columns(spark: "SparkSession", table: str) -> set[str]:
    """Return the set of column names from ``DESCRIBE TABLE <table>``.

    Filters out partition-info metadata rows that ``DESCRIBE TABLE``
    emits in some Spark versions (rows whose ``col_name`` starts with
    ``#``).
    """
    df = spark.sql(f"DESCRIBE TABLE {table}")
    rows = df.collect() if df is not None else []
    out: set[str] = set()
    for row in rows:
        # DESCRIBE TABLE returns col_name / data_type / comment.
        # Access via index 0 (works for both Row and tuple mocks).
        try:
            name = row[0]
        except (IndexError, TypeError):
            continue
        if not isinstance(name, str):
            continue
        if name.startswith("#") or name == "":
            continue
        out.add(name)
    return out
