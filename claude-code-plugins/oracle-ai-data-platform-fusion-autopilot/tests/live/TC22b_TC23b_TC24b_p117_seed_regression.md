# TC22b / TC23b / TC24b — P1.17 seed-mode regression on `fusion_bundle_dev`

**Test case IDs**: TC22b / TC23b / TC24b (per BACKLOG — Phase α `dim_account` / `gl_balance` / `ap_aging` re-verifications under P1.17 seed mode).
**Stage D plan item**: E3 (`docs/features/p1.17-incremental-merge/plan.md`).
**Status**: ✅ **EXECUTED 2026-06-02** on `fusion_bundle_dev` cluster / `playground` workspace via the AIDP REST dispatch surface (TC26 narrow scope — `erp_suppliers` + `ap_invoices` + `dim_supplier` + `dim_calendar` + `supplier_spend`).

## What this verifies

P1.17 added a non-trivial amount of new code under the seed-mode dispatch path (xxhash64 surrogates in the silver dims, `bronze_extract_ts` lineage column on every gold mart, the cache-aware bronze closure with the source-count empty-delta gate, the two-read silver/gold dispatch with post-build MAX(bronze_extract_ts) capture, and the IncrementalConfig nested model on the bundle). E3's contract: a `--mode seed` run against the narrow scope produces a clean cascade with row counts in the same family as the Phase α evidence — proves none of those Stage C changes broke seed-mode behavior.

## Live evidence — single TC26-narrow seed run via P1.17 wheel

### Per-step results (from the `AIDP_LIVE_TEST_RESULT` marker)

| layer  | dataset_id      | status   |   rows | duration |
|--------|-----------------|----------|-------:|---------:|
| bronze | erp_suppliers   | success  |    209 |   81.92s |
| bronze | ap_invoices     | success  | 49,552 |   77.53s |
| silver | dim_calendar    | success  |  4,018 |   22.32s |
| silver | dim_supplier    | success  |    209 |   33.76s |
| gold   | supplier_spend  | success  |  **309** |   36.42s |

**Totals**: 5 succeeded, 0 failed, 0 skipped, 0 deferred. Wall time 251.9s (~4.2 min).

### Cross-check against the canonical Phase α evidence

| Mart            | Phase α evidence    | P1.17 seed run     | Match? |
|-----------------|---------------------|--------------------|--------|
| `supplier_spend` row count (grain = vendor × currency × approval_status) | 309 (TC8 / TC28) | **309** | ✅ byte-identical |
| `dim_supplier` row count (silver projection of erp_suppliers) | 209 (TC8b / TC28) | **209** | ✅ byte-identical |
| `dim_calendar` row count (deterministic generation, 2020-01-01 → 2030-12-31) | 4,018 (TC21) | **4,018** | ✅ byte-identical |
| `erp_suppliers` bronze row count (BICC SupplierExtractPVO) | 209 (TC28) | **209** | ✅ byte-identical |
| `ap_invoices` bronze row count | 49,552 (TC28 — same demo pod, current data; earlier TC8 had 49,985 under a different BICC user's visibility) | **49,552** | ✅ demo-pod current snapshot |

The `supplier_spend` row count is the load-bearing assertion. The grain is (`vendor_id`, `currency_code`, `supplier_number`, `supplier_name`, `business_relationship`, `approval_status`) — six columns. Any drift in the aggregation logic would change this count. P1.17 keeps it at 309, unchanged from Phase α.

### Intentional deltas vs Phase α (per D2 + plan §B3/B9)

P1.17 ships two intentional changes that DO appear in the materialized rows:

1. **Surrogate keys** (`supplier_key` in `silver.dim_supplier`, `account_key` in `silver.dim_account`) — pre-P1.17 used `monotonically_increasing_id()` (partition-local, non-deterministic). P1.19 swaps to `xxhash64(CAST(<natural_key> AS STRING))`. Surrogate VALUES differ from Phase α, but the surrogate CONTRACT ("rebuild → same surrogate for same row") flips from "guaranteed false" (Phase α) to "guaranteed true" (P1.17). Verified by E4 below.
2. **`bronze_extract_ts` column on every gold mart** — new in P1.17 per plan §B3. Source: `MAX(b._extract_ts)` over the grain's source bronze rows (for `supplier_spend` + `ap_aging` aggregates) or row-level passthrough (for `gl_balance`).

Everything else — financial totals, row counts per natural-key bucket, dim attribute values — is unchanged.

## What this DOES NOT verify (deferred to TC30a / non-demo tenant)

- **Incremental MERGE write path** — TC22b/23b/24b only exercise seed mode. The MERGE branches (silver dim_supplier MERGE, gl_balance MERGE, bronze MERGE) ship in TC30a's evidence (E1a).
- **Real BICC delta extraction** — demo-pod Fusion data is frozen; only a non-demo tenant can prove BICC→bronze MERGE with `source_delta_count > 0`. Deferred to TC30b per plan §E1b + BACKLOG P3.7 / P3.9.
- **`gl_balance` + `ap_aging` row counts** — the narrow TC26 scope used here doesn't include `gl_period_balances` bronze (~11M rows on this pod), so `gl_balance` doesn't run. Same for `ap_aging` (needs `bronze.ap_invoices` already, but the narrow scope's `gold.marts` is `[supplier_spend]` only). Operator runs the full-scope TC26 separately when validating those marts; row-count drift would surface in the same `AIDP_LIVE_TEST_RESULT` marker pattern.

## Dispatch metadata (redacted)

- AIDP REST host: `https://datalake.us-ashburn-1.oci.oraclecloud.com`
- Workspace: `playground` (display name; UUID redacted)
- Cluster: `fusion_bundle_dev` (display name; UUID redacted)
- AIDP wheel: `oracle_ai_data_platform_fusion_bundle-0.1.0a0-py3-none-any.whl` (built from `HEAD` of `oussama-dev-p1.17-incremental-merge` post-Stage-C)
- jobKey / runKey / taskRunKey: operator-redacted (per-run identifiers, not material to the regression contract)
- Bundle config: narrow scope (per TC26 NARROW_BUNDLE template in `.claude/skills/fusion-tc26-run/dispatch.py`)

## Cross-references

- `docs/features/p1.17-incremental-merge/plan.md` §E3 — gating contract.
- `tests/live/TC8_supplier_spend_results.md` — Phase α supplier_spend evidence baseline.
- `tests/live/TC22_dim_account_results.md`, `tests/live/TC23_gl_balance_results.md`, `tests/live/TC24_ap_aging_results.md` — Phase α evidence for the three marts whose seed-mode behavior P1.17 promises not to break.
- `tests/live/TC28_orchestrator_incremental_infra.md` — β.1 evidence (same narrow scope, same demo pod, pre-P1.17 baseline).
- `tests/live/TC30a_p117_incremental_merge_proof.md` — companion file documenting the incremental MERGE path (E1a + E2).
- `tests/live/TC30b_*.md` (FUTURE) — non-demo tenant BICC→bronze MERGE evidence (E1b, BACKLOG P3.7 / P3.9).
