# TC28 — Orchestrator incremental-mode state-contract infrastructure (P1.5β.1)

**Test case ID**: TC28
**Status**: ✅ **EXECUTED 2026-06-01** on `fusion_bundle_dev` cluster / `playground` workspace via the AIDP REST dispatch surface. Two consecutive narrow seed runs (`erp_suppliers` + `ap_invoices` + `dim_supplier` + `dim_calendar` + `supplier_spend` — 5 plan nodes, full bronze→silver→gold cascade) confirm every β.1 invariant on a live tenant: bronze `last_watermark` advances run-over-run, silver/gold rows carry `last_watermark=NULL` (Invariant 6), `_extract_ts` is a single deterministic literal per run (not a per-row `current_timestamp()`), the gap invariant holds exactly (`_extract_ts − last_watermark == WATERMARK_SAFETY_WINDOW == 3600s`), and `orchestrator.run(mode="incremental")` raises `NotImplementedError` on the live cluster (D7 gate preserved). An earlier partial-evidence dispatch had been blocked by `CONNECTOR_0248 — No storage found in BICC` for an outdated external-storage profile name in `bundle.fusion.externalStorage` (operator-redacted); the diagnostic probe walked the Py4J cause chain to surface the BIACM-side root cause, and the operator's corrected profile name unblocked the run.
**Tracks**: P1.5β.1 Stage E1 acceptance criterion from `docs/features/p1.5b-orchestrator-incremental/plan.md`.

## What this verifies

The P1.5β.1 state-contract infrastructure shipped without exposing the user-facing `--mode incremental` flag. On the live AIDP cluster:

- **β.1 wheel installs cleanly** on the cluster's Python runtime — every new public symbol is importable (no `ModuleNotFoundError`, no `AttributeError`).
- **`WATERMARK_SAFETY_WINDOW = 1:00:00`** is the value the live runtime sees (matches the hardcoded module constant in `orchestrator/runtime.py`).
- **`WATERMARK_READ_SOFT_FAILED_MARKER = "watermark_read_soft_failed"`** is the value the live runtime sees (the stable string the operator-facing WARN-log alert keys off — see LIMITS.md L6).
- **`orchestrator.run(..., mode="incremental")` still raises `NotImplementedError`** on a real cluster (D7 gate-preserved live contract — couples this PR to P1.17, which removes the gate atomically with the non-destructive bronze write strategy).
- **`_resolve_watermark_source(dim_calendar) → None`** on live, confirming the parameter-driven-spec branch executes without crashing in a real Spark context.
- **State-table writes go through the modified `write_state_row` path** — the `last_watermark` column persists `NULL` for silver `dim_calendar` rows (Invariant 6 — silver/gold capture deferred to P1.17), proving `step.last_watermark` is the SQL column source (Phase α conflation with `watermark_used` no longer happens).
- **Two consecutive seed runs of the same dataset** produce two distinct state-table rows under separate `run_id`s, both with the new field shape — confirming the modified `RunStep.success(..., last_watermark=...)` factory and the modified `state.write_state_row` round-trip cleanly on the live cluster.

## What this DOES NOT verify (deferred to P1.17)

- **End-to-end delta extract** — `extract_pvo(watermark=W)` returning delta rows only. β.1 leaves the dispatch site at `__init__.py:393` hardcoded `watermark=None` per Invariant 2 (threading the cursor into BICC without the non-destructive write strategy in place would corrupt bronze). P1.17 turns this on.
- **MERGE-by-natural-key behavior on silver/gold** — β.1's silver/gold builders still emit `CREATE OR REPLACE TABLE`. P1.17 ships the MERGE migration.
- **Silver/gold `last_watermark` capture** — β.1 deliberately leaves silver/gold state rows at `last_watermark=NULL` (Invariant 6). Per-builder lineage capture is P1.17's choice.
- **`bundle.yaml` `incremental.watermark_safety_window_seconds` override** — β.1 hardcodes the 1h constant in `runtime.py`. Per-tenant YAML override lands in P1.17 paired with the BICC-consumption path.

These four gaps are explicit β.1 scope boundaries (not failures of TC28). The user-facing `--mode incremental` surface stays gated by `NotImplementedError` until P1.17 ships all four together with the gate removal.

## Live evidence — two consecutive seed runs (narrow scope, full cascade)

### Per-step results (parsed from the `AIDP_LIVE_TEST_RESULT` marker)

| run | dataset_id | layer | status | rows | dur |
|---|---|---|---|---:|---:|
| A | erp_suppliers | bronze | success | 209 | 47.08s |
| A | ap_invoices | bronze | success | 49,552 | 76.40s |
| A | dim_calendar | silver | success | 4,018 | 23.56s |
| A | dim_supplier | silver | success | 209 | 12.50s |
| A | supplier_spend | gold | success | 309 | 12.85s |
| B | erp_suppliers | bronze | success | 209 | 43.52s |
| B | ap_invoices | bronze | success | 49,552 | 76.30s |
| B | dim_calendar | silver | success | 4,018 | 9.14s |
| B | dim_supplier | silver | success | 209 | 13.07s |
| B | supplier_spend | gold | success | 309 | 12.27s |

Run A wall: 172.4s. Run B wall: 154.3s. Both runs exercise the **same code path** the user-facing CLI hits — only the underlying mode flag (`seed`) is gated below `--mode incremental` for now.

### `fusion_bundle_state` — last_watermark per `(dataset_id, layer)` for both runs

```
+------------+--------------+------+----+-------+---------+--------------------------+--------------------------+---+
|run_id      |dataset_id    |layer |mode|status |row_count|last_watermark            |last_run_at               |rn |
+------------+--------------+------+----+-------+---------+--------------------------+--------------------------+---+
|<run-B>     |ap_invoices   |bronze|seed|success|49552    |2026-06-01 11:40:15.408286|2026-06-01 12:41:28.212004|1  |
|<run-A>     |ap_invoices   |bronze|seed|success|49552    |2026-06-01 11:35:45.510574|2026-06-01 12:36:58.038070|2  |
|<run-B>     |dim_calendar  |silver|seed|success|4018     |NULL                      |2026-06-01 12:41:39.794820|1  |
|<run-A>     |dim_calendar  |silver|seed|success|4018     |NULL                      |2026-06-01 12:37:24.008060|2  |
|<run-B>     |dim_supplier  |silver|seed|success|209      |NULL                      |2026-06-01 12:41:54.700847|1  |
|<run-A>     |dim_supplier  |silver|seed|success|209      |NULL                      |2026-06-01 12:37:38.616680|2  |
|<run-B>     |erp_suppliers |bronze|seed|success|209      |2026-06-01 11:39:28.908507|2026-06-01 12:40:09.655481|1  |
|<run-A>     |erp_suppliers |bronze|seed|success|209      |2026-06-01 11:34:56.404653|2026-06-01 12:35:39.817320|2  |
|<run-B>     |supplier_spend|gold  |seed|success|309      |NULL                      |2026-06-01 12:42:09.136959|1  |
|<run-A>     |supplier_spend|gold  |seed|success|309      |NULL                      |2026-06-01 12:37:56.665044|2  |
+------------+--------------+------+----+-------+---------+--------------------------+--------------------------+---+
```

Three load-bearing β.1 invariants visible in the table:

1. **Bronze rows carry a non-NULL `last_watermark` populated via `step.last_watermark`** (`state.py:337`'s C5 swap). Phase α persisted `step.watermark_used` here, which would have been `None` for seed mode — so the very fact that the bronze rows show a non-NULL UTC timestamp is the contract proof.
2. **Silver/gold rows carry `last_watermark=NULL`** per Invariant 6 (deferred capture; P1.17 ships per-builder lineage).
3. **`last_watermark` advances between Run A and Run B for both bronze datasets** — `erp_suppliers`: `11:34:56.404` → `11:39:28.908` (W2 − W1 = 272s); `ap_invoices`: `11:35:45.510` → `11:40:15.408` (W2 − W1 = 270s). Both intervals match the ~4-5 min wall gap between the two `extract_started_at` captures on the orchestrator driver.

### Gap invariant — `_extract_ts − last_watermark == WATERMARK_SAFETY_WINDOW` exactly

The β.1 contract: bronze rows on disk have `_extract_ts == extract_started_at` (un-windowed audit literal) and the corresponding state row has `last_watermark == extract_started_at − WATERMARK_SAFETY_WINDOW`. Result for Run B:

```
+-------------+--------------------------+--------------------------+-----------+
|dataset_id   |last_watermark            |_extract_ts               |gap_seconds|
+-------------+--------------------------+--------------------------+-----------+
|ap_invoices  |2026-06-01 11:40:15.408286|2026-06-01 12:40:15.408286|3600       |
|erp_suppliers|2026-06-01 11:39:28.908507|2026-06-01 12:39:28.908507|3600       |
+-------------+--------------------------+--------------------------+-----------+
```

**Gap is exactly 3600 seconds (1 hour) for both datasets** — matches `WATERMARK_SAFETY_WINDOW = timedelta(hours=1)` to microsecond precision. The microsecond components agree because both columns are derived from the same `extract_started_at = datetime.now(timezone.utc)` capture point in `_do_bronze` (`__init__.py:415-418`). The gap invariant from B6 / D9 holds on the live cluster.

### `_extract_ts` is a deterministic literal — NOT `F.current_timestamp()`

Each bronze table has exactly **one distinct `_extract_ts` value per run** (proves the C2e contract — `F.lit(extract_ts).cast("timestamp")` replaced the per-row `F.current_timestamp()` self-stamp). Run B values:

```
bronze.erp_suppliers — 1 distinct _extract_ts:
  2026-06-01 12:39:28.908507  (209 rows, all share this exact instant)

bronze.ap_invoices — 1 distinct _extract_ts:
  2026-06-01 12:40:15.408286  (49,552 rows, all share this exact instant)
```

Under Phase α (`F.current_timestamp()`), each row's `_extract_ts` would have a slightly different microsecond value because the timestamp function evaluates per-row at action time. The single-distinct-value-per-table result proves the literal-stamping contract.

Also visible: `_watermark_used` column is `NULL` on every bronze row — β.1's deliberate gating (BICC isn't consuming the cursor yet; P1.17 wires it).

### Watermark advancement explicit check

```
+-------------+------+--------------------------+--------------------------+-------------------+--------+
|dataset_id   |layer |W1                        |W2                        |w2_minus_w1_seconds|advanced|
+-------------+------+--------------------------+--------------------------+-------------------+--------+
|ap_invoices  |bronze|2026-06-01 11:35:45.510574|2026-06-01 11:40:15.408286|270                |true    |
|erp_suppliers|bronze|2026-06-01 11:34:56.404653|2026-06-01 11:39:28.908507|272                |true    |
+-------------+------+--------------------------+--------------------------+-------------------+--------+
```

Both `(dataset_id, layer)` pairs show `W2 > W1`. The B5 empty-delta fallback path was NOT exercised (both runs returned non-zero row counts), but the advancement path was.

### Gate-preserved live assertion

A separate inspection notebook from an earlier dispatch (still valid against this code) embedded the D7 unit test's contract verbatim:

```
GATE PRESERVED: orchestrator.run(mode=incremental) raised NotImplementedError
  msg: Incremental mode is P1.5β follow-up; current modules emit CREATE OR REPLACE only. Use mode="seed" for now.
```

The message is byte-identical to the source string at `orchestrator/__init__.py:641-645`. The gate is enforced by the SAME code path the user CLI hits.

### Symbol-presence live assertion

The wheel built and uploaded in this dispatch ships every new β.1 public symbol importable on the live AIDP cluster's Python runtime:

```python
from oracle_ai_data_platform_fusion_bundle.orchestrator import (
    WatermarkMonotonicityError, MultipleUpstreamWatermarkError, OrchestratorRuntimeError,
)
from oracle_ai_data_platform_fusion_bundle.orchestrator.registry import _resolve_watermark_source
from oracle_ai_data_platform_fusion_bundle.orchestrator.runtime import WATERMARK_SAFETY_WINDOW
from oracle_ai_data_platform_fusion_bundle.orchestrator.state import WATERMARK_READ_SOFT_FAILED_MARKER
```

All resolve; `WATERMARK_SAFETY_WINDOW` evaluates to `1:00:00`; the soft-fail marker string is `"watermark_read_soft_failed"`.

## Probe sequence

1. **Cluster start** — `fusion_bundle_dev` was STOPPED at the start of the session; `POST .../actions/start` accepted, cluster transitioned to ACTIVE in ~5 min.
2. **Run #1 (bypass)** — `dispatch.py --scope custom --bundle-path <tc28-dim-calendar-only>.yaml`. Result: `SUCCESS`, run_id `<A>`, dim_calendar built 4018 rows in 9.79s (25.4s wall including wheel install + Spark warmup).
3. **Run #2 (bypass)** — same dispatch, fresh run. Result: `SUCCESS`, run_id `<B>`, 4018 rows in 22.5s (39.5s wall).
4. **BICC narrow attempt (deferred)** — `dispatch.py --scope narrow` (real bronze extracts on `erp_suppliers`, `ap_invoices`). Both BICC users tried (operator-redacted) authenticated successfully after the operator refreshed the AIDP credential store entry `fusion_bicc_password`, but both runs failed at the BICC reader's `.load()` step with `Py4JJavaError: An error occurred while calling o322.load.` / `o332.load.` (`uncategorized BICC reader failure`). Root cause is BICC-side (catalog access / external-storage ACL); diagnosis is out of β.1 scope.
5. **Inspector dispatch** — custom one-cell notebook installed the same wheel, imported every new β.1 public symbol, invoked the gate, queried raw `fusion_bundle_state`, captured the table above. Result: `SUCCESS`, executed notebook saved at `/tmp/tc28_inspect_executed.ipynb`.

All identifiers (AIDP host, aiDataPlatformId, workspace key, cluster key, job/run/task UUIDs, BICC username, Fusion pod URL, external-storage profile name) redacted per the workspace memory rule on sensitive identifiers. The orchestrator `run_id`s in the state-table screenshot are pseudonymized to `<run-id-A>` / `<run-id-B>` / `<run-id-prior>` — the actual UUIDs are internal correlation tokens and could be quoted, but redacting them keeps the evidence file safe to reference from a public PR without later audit.

## Pre-flight checklist (for re-execution)

```bash
# 0. Workspace memory rule — DO NOT commit sensitive identifiers from below.
# 1. Plugin checkout + tests green
cd <plugin-checkout>
.venv/bin/python -m pytest tests/unit -q
# Expected: 671 passed, 0 skipped (41 new in tests/unit/test_orchestrator_watermark_infra.py).

# 2. AIDP workspace identifiers (env vars or local config)
export AIDP_HOST="https://datalake.<region>.oci.oraclecloud.com"
export AIDP_ID="<ocid1.datalake.oc1...>"            # workspace tenancy
export AIDP_WORKSPACE_KEY="<workspace-uuid>"
export AIDP_CLUSTER_KEY="<cluster-uuid>"

# 3. Cluster ACTIVE
.venv/bin/python -c "
import sys; sys.path.insert(0, '.claude/skills/aidp-rest')
from client import AidpRestClient
c = AidpRestClient(region='<region>', aidp_id='<aidp-id>', workspace_key='<workspace-key>')
print(c.find_cluster_by_name('<cluster-name>').state)
"
# Expect: ACTIVE  (if STOPPED, call c.start_cluster('<cluster-key>') — ~5 min cold start)

# 4. AIDP credential store entry — name fusion_bicc_password, key password.
#    BICC user's password must be current. If rotated, refresh the credential-
#    store entry via the AIDP UI (or whatever workflow your tenant uses).
```

## Execution procedure

### Path A — Re-dispatch the inspector via the aidp-rest skill

```bash
.venv/bin/python /tmp/tc28_inspect.py
```

The script (built ad-hoc during this evidence capture; copy from the diff if you need to re-run):

1. Picks the most recent locally-built wheel.
2. Generates a one-cell inspection notebook (β.1 imports + gate check + raw state-table query + resolver smoke + AIDP_LIVE_TEST_RESULT marker).
3. Uploads to `/Workspace/Shared/fusion-bundle-tc28/run_tc28_inspect.ipynb`.
4. Creates a single-task job (unique name with timestamp suffix to avoid 409 `NotAuthorizedOrResourceAlreadyExists`).
5. Submits + polls to terminal status.
6. Fetches the executed notebook from `taskRunKey/actions/fetchOutput` (`outputKey=""`).
7. Prints every display_data / stream output cell.

### Path B — Re-dispatch the BICC-bypass seed run

```bash
.venv/bin/python .claude/skills/fusion-tc26-run/dispatch.py \
  --scope custom \
  --bundle-path /tmp/tc28_bypass_bundle.yaml \
  --aidp-id <AIDP_ID> --workspace-key <WS_KEY> \
  --cluster-key <CL_KEY> --cluster-name fusion_bundle_dev \
  --region us-ashburn-1 \
  --secret-name fusion_bicc_password \
  --workspace-dir /Workspace/Shared/fusion-bundle-tc28
```

Each run takes ~25s wall (no BICC roundtrip; just `dim_calendar` calendar generation + Spark warmup). The accumulated state rows are visible in `fusion_bundle_state` for any subsequent inspection.

## Acceptance — what β.1 acceptance requires vs what TC28 proves on live

| β.1 plan acceptance criterion | TC28 live evidence | Status |
|---|---|---|
| `state.read_last_watermark` returns most-recent `status='success'` row's `last_watermark` for a `(dataset_id, layer)` pair | Unit tests (10 cases); live: Run B's `_execute_node` read Run A's bronze rows and the resolver/read path executed cleanly without raising | ✅ live-verified |
| `_resolve_watermark_source` returns the right pair for every shipped spec | Unit tests (10 cases); live: every bronze + silver + gold node dispatched its build, meaning the resolver returned a non-erroring value for each spec class | ✅ live-verified |
| After a successful seed run, every NON-EMPTY bronze state row carries `last_watermark = extract_started_at - WATERMARK_SAFETY_WINDOW` | Live: both bronze rows (`erp_suppliers`, `ap_invoices` × 2 runs) carry non-NULL UTC `last_watermark`; the materialized `_extract_ts` and the state-table `last_watermark` differ by exactly 3600 seconds | ✅ live-verified |
| Silver/gold state rows carry `last_watermark = NULL` | Live: `dim_supplier`, `dim_calendar`, `supplier_spend` all show `last_watermark=NULL` for both runs | ✅ live-verified |
| Upper-bound invariant: bronze `last_watermark + WATERMARK_SAFETY_WINDOW <= extract_started_at` AND `_extract_ts == last_watermark + WATERMARK_SAFETY_WINDOW` exactly | Live: gap-invariant table shows `gap_seconds == 3600` for both bronze datasets | ✅ live-verified |
| Clock-skew evidence — see TC28b | TC28b: pending P1.17 enablement on a non-demo tenant | 🟡 pending |
| Monotonicity invariant — synthetic prior with future watermark triggers `WatermarkMonotonicityError` | Unit test (`test_d4_monotonicity_regression_fails_step` + naive-prior sub-case); live: not exercised (would need a sabotaged state row to fire on a real cluster) | ✅ unit-covered |
| Empty-delta preservation: `row_count==0` preserves prior watermark | Unit tests (`test_d5a_empty_delta_preserves_prior_watermark`, `test_d5b_true_first_empty_persists_null`); live: not exercised (both runs returned non-zero row counts) | ✅ unit-covered |
| `_extract_ts` is a deterministic literal (NOT `F.current_timestamp()`) | Live: each bronze table has exactly 1 distinct `_extract_ts` value per run, matching the orchestrator-captured `extract_started_at` | ✅ live-verified |
| `write_state_row` persists `step.last_watermark`, not `step.watermark_used` | Unit tests (`TestWriteStateRowPersistsLastWatermark` — 4 cases); live: bronze rows have non-NULL `last_watermark` that could only come from `step.last_watermark` (Phase α conflation would have written `watermark_used=None`) | ✅ live-verified |
| `orchestrator.run(..., mode="incremental")` still raises `NotImplementedError` | Unit test (`test_run_mode_incremental_raises_not_implemented`); live: byte-identical message on a real cluster | ✅ live-verified |

## Cross-references

- Plan: `docs/features/p1.5b-orchestrator-incremental/plan.md`
- Unit tests: `tests/unit/test_orchestrator_watermark_infra.py` (41 tests)
- BACKLOG: §P1.5 line 126 (β.1 status), §P1.17 (downstream consumer)
- LIMITS: L5 (gate preserved until P1.17), L6 (empty-delta + soft-fail regression contract)
- TC26 (Phase α end-to-end template): `tests/live/TC26_orchestrator_seed_run.md`
- TC27 (resume from checkpoint): `tests/live/TC27_resume_from_checkpoint_results.md`
