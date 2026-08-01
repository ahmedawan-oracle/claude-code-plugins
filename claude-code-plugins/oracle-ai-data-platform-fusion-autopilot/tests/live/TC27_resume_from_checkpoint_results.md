# TC27 — Resume from checkpoint (P1.5α-fix21 acceptance evidence)

**Test case ID**: TC27
**Status**: ✅ **EXECUTED 2026-05-23 21:30 UTC**, Phase 3 **RE-RUN 2026-05-23 22:00 UTC** against a live AIDP cluster on a Fusion demo tenant via OCI-signed REST dispatch. Coordinates redacted per the TC26 evidence convention; full identifiers held by the dispatching operator. Phase 3 re-run captured post-`a7b9f62` row_count carry-forward behavior.
**Tracks**: `P1.5α-fix21` acceptance criterion in BACKLOG.md — "deliberate kill-mid-run + `--resume` produces a complete pipeline in (resume time) ≪ (clean run time)."

## What this verifies (all PASS)

| # | Assertion | Evidence |
|---|---|---|
| 1 | `aidp-fusion-bundle run --inline --resume <run_id>` end-to-end works on a live tenant | Phase 3 ran successfully, all 5 plan nodes terminal-success |
| 2 | **Original `run_id` preserved** — same UUID across original + resume; medallion `<layer>_run_id` invariant intact | Phase 2 and Phase 3 emit identical `run_id` in their RunSummary |
| 3 | Succeeded nodes carry forward as `resumed_skipped` with `skip_reason='resume-skip'`, `duration_seconds=0.0` | 3 nodes carried forward (erp_suppliers + ap_invoices + dim_calendar) |
| 4 | Failed + cascade-skipped nodes re-attempt + succeed on resume | dim_supplier (silver) + supplier_spend (gold) re-dispatched, both succeeded |
| 5 | Resume runtime ≪ clean runtime | **Phase 3 67.7s vs Phase 1 335.8s = 5× speedup** |
| 6 | Plan hash identical across all rows under the resumed run_id (drift gate didn't fire — same bundle) | All 10 rows share the same `plan_hash` |
| 7 | State table is append-only on resume — multi-row per `(run_id, dataset_id)` | Cross-tab below shows 2 rows per dataset under the same run_id |
| 8 | `fusion_bundle_state_latest` projection gives one row per dataset with terminal state | Window-projected table below shows 5 rows, terminal status per dataset |

## Coordinates (redacted)

```
aidp-id        : <REDACTED — AIDP datalake OCID held by the operator>
workspace-key  : <REDACTED — workspace UUID>
cluster-key    : <REDACTED — cluster UUID>
fusion pod     : <REDACTED — Fusion demo pod base URL>
fusion user    : <REDACTED — BICC user>
storage profile: <REDACTED — BICC External Storage profile name>
secret entry   : <REDACTED — AIDP credential store entry name>
bundle         : tc26-narrow-probe (2 bronze + 2 silver + 1 gold)
```

Bundle scope (narrow): `erp_suppliers`, `ap_invoices` (bronze) → `dim_supplier`, `dim_calendar` (silver) → `supplier_spend` (gold). Same shape as `dispatch.NARROW_BUNDLE` in `.claude/skills/fusion-tc26-run/dispatch.py`.

## Run identifiers

Phase | run_id | JobRun terminal | Wall time
---|---|---|---
1 — clean baseline   | `5c03905b-…` (Phase 1 orchestrator UUID — full UUID safe to share, internal correlation only) | SUCCESS | **335.8s**
2 — induced failure  | `6bebf134-…` (Phase 2 orchestrator UUID) | SUCCESS (cluster) / 1 failed step | 278.9s
3 — resume           | `6bebf134-…` (SAME as Phase 2 — preserved) | SUCCESS | **67.7s**

Full orchestrator UUIDs available in the operator's local executed-notebook captures. JobRun keys are redacted per TC26 convention (cluster-side identifiers).

**Δt_resume / Δt_clean = 67.7s / 335.8s ≈ 0.20** — resume is 5× faster than re-running from scratch on this narrow scope. On a full-finance bundle (~11 bronze with `gl_period_balances` ~10M rows + ~25min wall), the speedup amplifies further; the narrow probe demonstrates the contract.

## Per-phase RunSummary

### Phase 1 — clean baseline

```
PHASE_1_CLEAN wall=335.8s
  bronze  erp_suppliers             success                         rows=       209  dur=91.63s
  bronze  ap_invoices               success                         rows=     49552  dur=104.53s
  silver  dim_calendar              success                         rows=      4018  dur= 8.71s
  silver  dim_supplier              success                         rows=       209  dur=10.14s
  gold    supplier_spend            success                         rows=       309  dur=11.05s

5 success, 0 failed, 0 skipped, 0 resumed_skipped, 0 deferred
total_duration=226.06s  wall=335.79s
```

### Phase 2 — induced failure (monkeypatch dim_supplier silver builder → RuntimeError)

```
PHASE_2_INDUCED_FAIL wall=278.9s
  bronze  erp_suppliers             success                         rows=       209  dur=80.98s
  bronze  ap_invoices               success                         rows=     49552  dur=110.32s
  silver  dim_calendar              success                         rows=      4018  dur=15.18s
  silver  dim_supplier              failed                          rows=         -  dur= 0.00s  err=RuntimeError("TC27 induced failure: …")
  gold    supplier_spend            skipped          [cascade]      rows=         -  dur= 0.00s

3 success, 1 failed, 1 skipped (cascade), 0 resumed_skipped, 0 deferred
```

Three datasets land terminal-success (their tables are on disk), one fails (dim_supplier), one cascade-skips (supplier_spend depends on dim_supplier).

### Phase 3 — resume

```
PHASE_3_RESUME wall=67.7s
  bronze  erp_suppliers             resumed_skipped  [resume-skip]  rows=         -  dur=0.00s
  bronze  ap_invoices               resumed_skipped  [resume-skip]  rows=         -  dur=0.00s
  silver  dim_calendar              resumed_skipped  [resume-skip]  rows=         -  dur=0.00s
  silver  dim_supplier              success                         rows=       209  dur=9.01s
  gold    supplier_spend            success                         rows=       309  dur=21.44s

2 success, 0 failed, 0 skipped, 3 resumed_skipped, 0 deferred
total_duration=30.45s  wall=67.66s
```

- `run_id` identical to Phase 2 — preserved on resume per the medallion `<layer>_run_id` invariant.
- 3 carry-forwards: `erp_suppliers`, `ap_invoices`, `dim_calendar` (all `success` in Phase 2 → `resumed_skipped` here).
- 2 re-dispatches: `dim_supplier` (Phase 2 `failed` → now `success`, 209 rows match baseline), `supplier_spend` (Phase 2 `skipped` cascade → now `success`, 309 rows match baseline).

## State-table evidence (queried inside Phase 3 notebook)

### Latest-per-`(run_id, dataset_id)` projection — post row_count carry-forward fix

After commit `a7b9f62` added `ResumeContext.succeeded_row_counts` + the
walk-back-past-NULL query, an all-succeeded re-resume against the same
`run_id` produced the projection below. Every resumed-skipped row now
inherits the original row_count (was NULL before the fix). Wall time
57.3s — no actual work, just 5 carry-forwards under the resumed run_id.

```
+--------------+------+---------------+---------+-----------+----------------+
|dataset_id    |layer |status         |row_count|skip_reason|duration_seconds|
+--------------+------+---------------+---------+-----------+----------------+
|ap_invoices   |bronze|resumed_skipped|49552    |resume-skip|0.0             |
|erp_suppliers |bronze|resumed_skipped|209      |resume-skip|0.0             |
|supplier_spend|gold  |resumed_skipped|309      |resume-skip|0.0             |
|dim_calendar  |silver|resumed_skipped|4018     |resume-skip|0.0             |
|dim_supplier  |silver|resumed_skipped|209      |resume-skip|0.0             |
+--------------+------+---------------+---------+-----------+----------------+
```

- One row per dataset (the projection collapses the multi-row history).
- All 5 rows share the same `plan_hash` (truncated for display) — drift gate didn't fire (same bundle).
- Row counts match Phase 1 baseline exactly (209 suppliers, 49552 ap_invoices, 4018 dim_calendar, 209 dim_supplier, 309 supplier_spend).
- `dim_supplier` traversed the trickier path (`failed` → `success` → `resumed_skipped`) and the walk-back query still recovered its 209-row count from the intermediate success row.

### Historical projection — pre row_count carry-forward fix

The initial Phase 3 run (commit `e9fc02e`) showed NULL row_counts for
the carry-forward rows; this was the bug the row_count fix addresses.
Kept here for the audit trail. Re-running Phase 3 against the same
run_id under commit `a7b9f62`+ gives the table above.

```
ap_invoices   bronze resumed_skipped NULL  ← bug: lost the original 49552-row count
erp_suppliers bronze resumed_skipped NULL  ← bug: lost the original 209-row count
dim_calendar  silver resumed_skipped NULL  ← bug: lost the original 4018-row count
```

### Cross-tab — full append-only history under the resumed `run_id`

```
+--------------+---------------+---------+
|dataset_id    |status         |row_count|
+--------------+---------------+---------+
|ap_invoices   |success        |1        |    ← Phase 2: bronze succeeded
|ap_invoices   |resumed_skipped|2        |    ← Phase 3 + Phase 3 rerun: carry-forwards
|dim_calendar  |success        |1        |    ← Phase 2: silver succeeded
|dim_calendar  |resumed_skipped|2        |    ← Phase 3 + Phase 3 rerun: carry-forwards
|dim_supplier  |failed         |1        |    ← Phase 2: monkeypatch raised
|dim_supplier  |success        |1        |    ← Phase 3: re-dispatch succeeded
|dim_supplier  |resumed_skipped|1        |    ← Phase 3 rerun: carry-forward
|erp_suppliers |success        |1        |    ← Phase 2: bronze succeeded
|erp_suppliers |resumed_skipped|2        |    ← Phase 3 + Phase 3 rerun: carry-forwards
|supplier_spend|skipped        |1        |    ← Phase 2: cascade-skipped
|supplier_spend|success        |1        |    ← Phase 3: re-dispatch succeeded
|supplier_spend|resumed_skipped|1        |    ← Phase 3 rerun: carry-forward
+--------------+---------------+---------+

15 rows total — original 5 + 5 from first resume + 5 from Phase 3 rerun
(post row_count carry-forward fix). Demonstrates re-resume safety AND
row_count walk-back-past-NULL on Phase 3 rerun.
```

This is exactly the append-only multi-row semantics LIMITS.md §L-Resume documents — consumers must read from `fusion_bundle_state_latest` or apply the latest-per-`(run_id, dataset_id)` window to get one row per dataset.

## Dispatcher

```bash
# All three phases in one shot (placeholders — operator fills in real values
# from their local `.aidp/aidp.config.yaml` + AIDP credential store):
.venv/bin/python .claude/skills/fusion-tc26-run/tc27_dispatch.py \
  --aidp-id        <AIDP-OCID> \
  --workspace-key  <WORKSPACE-UUID> \
  --cluster-key    <CLUSTER-UUID> \
  --cluster-name   <CLUSTER-DISPLAY-NAME> \
  --region         us-ashburn-1 \
  --fusion-service-url <FUSION-POD-URL> \
  --fusion-user        <BICC-USER> \
  --external-storage   <BICC-STORAGE-PROFILE> \
  --phases 1,2,3

# Or to retry just one phase, e.g. phase 3 against an existing failed run:
... --phases 3 --resume-run-id <R_resume_initial>
```

Executed notebooks + raw payloads are written to `/tmp/tc27-<timestamp>/` per dispatch — held locally on the operator's workstation.

## Known dispatcher notes

- **Marker-parse fragility on free-form error strings**: Phase 2's RunSummary includes an `error_message` field containing `repr(exc) = 'RuntimeError("…")'`. When this JSON marker is emitted via `print(json.dumps(...))` and captured into the notebook's `display_data text/plain`, the AIDP notebook runtime strips the JSON-escape backslashes from the nested quotes, producing invalid JSON for the dispatcher's marker parser. Fallback: extract `run_id` via a substring/regex pre-pass when the JSON load fails (Phase 2 evidence still recoverable from the notebook's pre-marker print). Tracked as a small dispatcher hardening item — non-blocking for TC27 acceptance.
- **First Phase 2 attempt aborted at preflight**: the original dispatcher monkeypatched `extractors.bicc.extract_pvo`, which also poisoned `preflight_bronze_schemas`'s schema-probe path and raised `BronzeSchemaProbeError` before any orchestrator work. Resolved by monkeypatching the `dim_supplier` silver builder instead (post-preflight boundary). Documented in `.claude/skills/fusion-tc26-run/tc27_dispatch.py:_INDUCED_FAIL_RUN_CELL`.

## Cross-references

- BACKLOG.md §P1.5α-fix21 — implementation tracking entry; this evidence closes the "live evidence (TC27 or extension to TC26 doc)" acceptance criterion.
- `docs/features/fix21-resume-from-checkpoint/plan.md` — local plan with full design decisions (untracked working notes).
- `LIMITS.md` §L-Resume — append-only multi-row state-table semantics (gitignored working notes).
- TC26 evidence file — baseline pacing reference for narrow-scope timings, redaction convention.
- PR #10 against `craxelfn/claude-code-plugins` — orchestrator + cli: P1.5α-fix21 — resume from checkpoint + chaos-test the retry classifier.

---

## Re-validation — TC27 on new pod / new BICC user (2026-06-02)

**Why re-run**: closing P1.5α on the new pod (`saasfademo1` test variant) + the new BICC user (`natalie.salesrep`) — same credentials TC26 just validated. Reproduces the resume contract on the fresh tenant/user combination.

**Phase summary**:

| Phase | run_id | JobRun terminal | Wall time |
|---|---|---|---|
| 1 — clean baseline   | `be76a33a-…` | SUCCESS | ~317s |
| 2 — induced failure  | `cead1282-…` | SUCCESS (cluster) / 1 failed step + 1 cascade-skipped | 217s |
| 3 — resume           | `cead1282-…` (preserved) | SUCCESS | **158.8s** |

**Δt_resume / Δt_clean = 158.8 / 317 ≈ 0.50** — resume is 2× faster than re-running from scratch on this narrow scope. (Lower amplification than 2026-05-23's 5× because Phase 3's re-run on this pod hit longer dim_supplier + supplier_spend build times — still well within the ≪ contract.)

### Phase 3 — resume (per-step)

```
PHASE_3_RESUME run_id=cead1282-a384-4dac-9cb5-9e8310179a86 wall=158.8s

  bronze  erp_suppliers             resumed_skipped  [resume-skip]  rows=       209  dur=0.00s
  bronze  ap_invoices               resumed_skipped  [resume-skip]  rows=     49552  dur=0.00s
  silver  dim_calendar              resumed_skipped  [resume-skip]  rows=      4018  dur=0.00s
  silver  dim_supplier              success                         rows=       209  dur=42.88s
  gold    supplier_spend            success                         rows=       309  dur=50.16s

succeeded=2  failed=0  skipped=0  resumed_skipped=3
```

### State-table latest-per-(run_id, dataset_id) projection

```
+--------------+------+---------------+---------+-----------+----------------+---------------+
|dataset_id    |layer |status         |row_count|skip_reason|duration_seconds|plan_hash_short|
+--------------+------+---------------+---------+-----------+----------------+---------------+
|ap_invoices   |bronze|resumed_skipped|49552    |resume-skip|0.0             |7383bb249334   |
|erp_suppliers |bronze|resumed_skipped|209      |resume-skip|0.0             |7383bb249334   |
|supplier_spend|gold  |success        |309      |NULL       |50.16           |7383bb249334   |
|dim_calendar  |silver|resumed_skipped|4018     |resume-skip|0.0             |7383bb249334   |
|dim_supplier  |silver|success        |209      |NULL       |42.88           |7383bb249334   |
+--------------+------+---------------+---------+-----------+----------------+---------------+
```

All 5 rows share `plan_hash_short=7383bb249334` — drift gate held across the kill-and-resume boundary.

### Append-only cross-tab — full history under the resumed run_id

```
+--------------+---------------+---------+
|dataset_id    |status         |row_count|
+--------------+---------------+---------+
|ap_invoices   |resumed_skipped|1        |
|ap_invoices   |success        |1        |
|dim_calendar  |resumed_skipped|1        |
|dim_calendar  |success        |1        |
|dim_supplier  |failed         |1        |
|dim_supplier  |success        |1        |
|erp_suppliers |resumed_skipped|1        |
|erp_suppliers |success        |1        |
|supplier_spend|skipped        |1        |
|supplier_spend|success        |1        |
+--------------+---------------+---------+
```

Two rows per dataset under one `run_id` — confirms the state table is append-only on resume, and the latest-per-(run_id, dataset_id) projection correctly resolves to the terminal status of each.

### Phase 2 marker-parse fallback (manual recovery)

Phase 2 hit the known `display_data` JSON-escape-stripping issue (documented in §Known dispatcher notes above). The dispatcher exited with code 1 after Phase 2 even though the cluster-side run succeeded perfectly. Manual recovery:

1. Read `/tmp/tc27-…/phase2_executed.ipynb`.
2. Extract `run_id` from the pre-marker per-step print (`PHASE_2_INDUCED_FAIL run_id=cead1282-…`).
3. Re-invoke dispatcher with `--phases 3 --resume-run-id cead1282-…` to skip phases 1+2 and resume directly.

This recovery path is part of the dispatcher's intended UX (`--resume-run-id` flag is documented for "re-running phase 3 after a transient failure during the first attempt"). The marker-parse fragility itself is a separate follow-up.

### Closes BACKLOG.md P1.5α-fix21 acceptance

This re-validation, combined with the 2026-05-23 run, satisfies the acceptance criterion:
> "deliberate kill-mid-run + `--resume` produces a complete pipeline in (resume time) ≪ (clean run time)."

P1.5α-fix21 flips from `[~]` → `[x]` in BACKLOG.md.
