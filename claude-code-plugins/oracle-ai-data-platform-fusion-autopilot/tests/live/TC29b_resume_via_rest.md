# TC29b — Resume via REST dispatch (P1.5ε-fix5 live evidence)

**Run date**: 2026-06-03
**Tenant**: `fusion_bundle_dev` cluster on `<aidp-workspace>`, region `us-ashburn-1`
**Plugin commit**: `aea53ac` (P1.5ε-fix5 source pre-extract_cell_errors hardening) + a follow-on commit shipping the stderr-stream support in `extract_cell_errors` (the live-evidence-driven discovery captured below).
**Dispatcher**: `.claude/skills/fusion-tc26-run/tc29b_resume_dispatch.py`
**Bundle**: narrow scope (2 bronze + 2 silver + 1 gold) — same template the TC27 evidence uses, so Phase wall times are directly comparable.

> **Identifier redaction**: per workspace `CLAUDE.md` redaction rule, OCIDs / workspace UUIDs / cluster UUIDs / pod URLs / BICC usernames / external-storage profile / secret entry names are replaced with placeholders below. Orchestrator-generated `run_id` values are shown as `<8-char-prefix>…` to preserve the Phase-2-→-Phase-3 identity proof while masking the full UUID. Job/jobRun UUIDs are masked entirely. The full unredacted log + executed notebooks live on the operator's workstation at `/tmp/tc29b-<timestamp>/`.

## Acceptance summary

| Acceptance criterion | Phase | Status |
|---|---|---|
| `--resume <run_id>` over REST succeeds end-to-end against a live tenant | 3 | ✅ |
| Resumed run carries the **original** `run_id` through `silver_run_id` / `gold_run_id` audit columns | 3 | ✅ — `silver_run_id matches on 209/209 rows`, `gold_run_id matches on 309/309 rows` |
| Datasets that succeeded pre-failure surface as `resumed_skipped` in the resume run | 3 | ✅ — 3× `resumed_skipped [resume-skip]` |
| Marker-parse fallback recovers `run_id` from the TC27-shaped malformed marker | 2 | ✅ — JSON parse failed at column 4427 ("Expecting ',' delimiter"); regex fallback extracted `run_id=66aa575a…` |
| Cell-error enrichment surfaces typed orchestrator exception over REST | 4 | ✅ (with discovery) — see §Phase 4 |
| Resume wall ≪ clean wall (cache + skip benefit) | 1, 3 | ✅ — 146.6s (resume) / 334.7s (clean) = **0.44×** |

## Wall-clock timings

| Phase | Description | Cluster wall (s) | Terminal status | Branch |
|---|---|---:|---|---|
| 1 | Clean baseline (productized `build_notebook`, `resume_run_id=None`) | 334.7 | SUCCESS | — |
| 2 | Induced failure (custom monkeypatched dim_supplier) | 231.7 | SUCCESS (job ran; orchestrator returned a partial-failure summary) | **degraded marker (regex fallback)** |
| 3 | Resume (productized `build_notebook`, `resume_run_id=<phase-2>`) | 146.6 | SUCCESS | — |
| 4 | Bad resume id (`--resume tc29b-not-a-real-id`) | 43.6 | FAILED (intended — `ResumeRunNotFoundError` on cell 3) | — |

Wheel build + Phase A preflight + per-phase upload overhead adds ≈ 30–60 s on top of cluster wall; the TC29b script logs each step at second granularity.

---

## Phase 1 — Clean baseline

Drives the productized `build_notebook(resume_run_id=None)` end-to-end. Validates the cluster is healthy and the narrow bundle's full bronze→silver→gold cascade produces the expected row counts.

```text
run_id=ef271fc1…
steps: 5 ok, 0 failed, 0 skipped, 0 deferred (213.0s reported / 286.0s wall)
  bronze  erp_suppliers             success                 rows=       209  dur=61.79s
  bronze  ap_invoices               success                 rows=     49552  dur=80.71s
  silver  dim_calendar              success                 rows=      4018  dur=11.41s
  silver  dim_supplier              success                 rows=       209  dur=30.69s
  gold    supplier_spend            success                 rows=       309  dur=28.39s
```

Marker parsed cleanly via `json.loads` (no failed step → no `repr(exc)` payload to trigger the TC27 trap). Establishes the reference row counts (209 / 49552 / 4018 / 209 / 309) that Phase 3 must reach under the resumed `run_id`.

---

## Phase 2 — Induced mid-cascade failure (the marker-parse fallback proof)

**Goal**: produce the exact failure shape that previously made REST resume operationally unusable — a partial-failure run whose marker JSON is corrupted by AIDP's `display_data text/plain` capture, so the laptop-side parser can't recover the `run_id` without surgery.

**Mechanism**: a custom notebook (only this phase bypasses `build_notebook` — see §Notes) monkeypatches `dim_supplier.build` to raise `RuntimeError("TC29b induced failure: simulating mid-run failure on dim_supplier silver build")` before dispatching `orchestrator.run(...)`. The cluster job reaches terminal SUCCESS because the run cell catches the exception inside `orchestrator.run`, builds a partial-failure `RunSummary`, and emits the marker via the productized `summary.to_marker_dict()`. The 5-row summary:

```text
PHASE_2_INDUCED_FAIL run_id=66aa575a… wall=204.6s
  bronze  erp_suppliers             success                         rows=       209  dur=66.32s
  bronze  ap_invoices               success                         rows=     49552  dur=71.35s
  silver  dim_calendar              success                         rows=      4018  dur=7.66s
  silver  dim_supplier              failed                          rows=         -  dur=11.05s err=RuntimeError("TC29b induced failure: simulating mid-run fail
  gold    supplier_spend            skipped          [cascade]      rows=         -  dur=0.00s
```

**The trap fires (live capture)**:

```text
[tc29b] poll_done: phase=2 terminal=SUCCESS wall_s=231.7
[tc29b] phase_2_marker_degraded: recovered_run_id=66aa575a-… raw_marker_preview={"schema_version": 1, "run_id": "66aa575a…
```

Inspection of the executed-notebook marker body confirms the exact JSON corruption site at character 4427:

```text
... "duration_seconds": 11.0507543240019,
"error_message": "RuntimeError("TC29b induced failure: simulating mid-run failure on dim_supplier silver build")",
                              ^^^ unescaped nested " — produced by AIDP's display_data capture stripping the JSON escapes from repr(exc)
"watermark_used": null, ...
```

`json.loads(body)` raises `json.JSONDecodeError: Expecting ',' delimiter: line 1 column 4427 (char 4426)` — the precise failure mode tracked in TC27 §"Known dispatcher notes" as a deferred hardening item. P1.5ε-fix5's new fallback path activates: `re.search(r'"run_id"\s*:\s*"([^"]+)"', body)` matches at character ≈ 30 (well before the corruption site), recovers `run_id="66aa575a…"`, and returns the synthetic sentinel `{"run_id": ..., "_marker_parse_failed": True, "_raw_marker": ...}`. The dispatcher converts the sentinel to `DispatchMarkerDegradedError(recovered_run_id="66aa575a…", ...)` — operator-facing path; the live TC29b harness reads the same sentinel and uses the recovered id to drive Phase 3 (proving the resume handle is reachable without notebook archaeology).

**Closes the TC27 deferred item.** Pre-fix5, this phase would have raised `DispatchMarkerMissingError` at the laptop side and an operator would have had to grep the executed notebook for the pre-marker `run_id=…` print to get the resume handle. Post-fix5, it's automatic.

---

## Phase 3 — Resume (the productized resume_run_id plumbing proof)

Drives the productized `build_notebook(resume_run_id="66aa575a…")` — the cluster-side run cell sees `orchestrator.run(..., resume_run_id="66aa575a…")` and reads the existing `fusion_bundle_state` rows. Per-step summary:

```text
run_id=66aa575a…  ← same run_id as Phase 2 (SOX-trail invariant)
steps: 2 ok, 0 failed, 0 skipped, 0 deferred (62.7s reported / 120.4s wall)
  bronze  erp_suppliers             resumed_skipped [resume-skip]  rows=       209  dur=0.00s
  bronze  ap_invoices               resumed_skipped [resume-skip]  rows=     49552  dur=0.00s
  silver  dim_calendar              resumed_skipped [resume-skip]  rows=      4018  dur=0.00s
  silver  dim_supplier              success                         rows=       209  dur=40.42s
  gold    supplier_spend            success                         rows=       309  dur=22.28s  (per verify-cell projection)
```

Counter check: 3× resumed_skipped (the datasets that succeeded in Phase 2) + 2× new success (the previously-failed dim_supplier re-attempts and the cascade-skipped supplier_spend re-dispatches) = 5 total, matching the Phase 1 baseline cardinality.

**Latest-per-(run_id, dataset_id) projection of `fusion_bundle_state` for `run_id=66aa575a…`** (verify-cell output):

```text
+--------------+------+----+---------------+---------+-----------+------------------+
|dataset_id    |layer |mode|status         |row_count|skip_reason|duration_seconds  |
+--------------+------+----+---------------+---------+-----------+------------------+
|ap_invoices   |bronze|seed|resumed_skipped|49552    |resume-skip|0.0               |
|erp_suppliers |bronze|seed|resumed_skipped|209      |resume-skip|0.0               |
|supplier_spend|gold  |seed|success        |309      |NULL       |22.27991683199798 |
|dim_calendar  |silver|seed|resumed_skipped|4018     |resume-skip|0.0               |
|dim_supplier  |silver|seed|success        |209      |NULL       |40.422965944999305|
+--------------+------+----+---------------+---------+-----------+------------------+
```

**SOX-trail audit-column proof** (verify-cell):

```text
SOX-trail silver dim_supplier        : silver_run_id matches on 209/209 rows
SOX-trail gold   supplier_spend      : gold_run_id matches on 309/309 rows
```

Both materialized layers' audit columns carry `66aa575a…` — the **original** `run_id` from Phase 2 — on every row, not a new run_id. The cluster-side `orchestrator.run(resume_run_id=...)` preserved the original identity through both new builds.

**Resume vs clean wall**: 146.6s / 334.7s = **0.44×** — Phase 3 finishes in less than half the time of Phase 1 because the three carry-forward datasets are no-ops.

---

## Phase 4 — Bad resume id (cell-error enrichment + live-evidence-driven follow-up)

**Goal**: validate that an invalid `--resume <id>` produces an operator-facing error block carrying the typed `ResumeRunNotFoundError` ename — no notebook archaeology required.

Drives `build_notebook(resume_run_id="tc29b-not-a-real-id")`. The cluster job fails at cell 3 because `state.read_resumable_state(...)` finds no rows for that run_id and raises:

```text
ResumeRunNotFoundError: --resume: no rows in fusion_bundle_state for run_id='tc29b-not-a-real-id'.
Check the value (operator typo?) or use `aidp-fusion-bundle status` to list recent run_ids.
```

— matching the exact message shape from `orchestrator/state.py:954-955` (the literal `evalue` that the Step 8 unit tests assert against).

### Live-evidence finding — `extract_cell_errors` had to be extended

The initial TC29b run revealed a contract gap that unit tests couldn't catch: **AIDP's notebook runtime emits cell exceptions as `output_type: stream, name: stderr` with the full Python traceback inlined as text, not as the documented `output_type: error`.** The synthetic unit-test fixtures in `test_dispatch_via_rest.py::TestRunFailedCellErrorEnrichment` use `output_type: error` (matching the nbconvert / vanilla-Jupyter shape), so the productized `extract_cell_errors` matched fine in tests but missed the cell-3 traceback on the real cluster.

Raw shape of Phase 4 cell-3 output (from the executed notebook):

```text
output_type: stream
name: stderr
text: "Command ID <run-key>_… failed with java.lang.RuntimeException: [Command …] failed with error:
       ---------------------------------------------------------
       ResumeRunNotFoundError                    Traceback (most recent call last)
       Cell In[17], line 3
       ...
       File /tmp/.../orchestrator/state.py:954, in read_resumable_state(spark, paths, run_id)
           953 if not rows:
       --> 954     raise ResumeRunNotFoundError(
           955         f"--resume: no rows in fusion_bundle_state for run_id={run_id!r}. ...
       ResumeRunNotFoundError: --resume: no rows in fusion_bundle_state for run_id='tc29b-not-a-real-id'. Check the value (operator typo?) or use `aidp-fusion-bundle status` to list recent run_ids."
```

### Fix applied in this branch

`extract_cell_errors` extended to recognize both shapes: the canonical `output_type: error` AND `output_type: stream, name: stderr` with a regex-matched `^<ExceptionClass>: <message>$` trailing line. Multi-line implementation: `(?m)` flag, `$` end-of-line, take the LAST match in `finditer()` so chained-exception tracebacks ("During handling of the above exception, another exception occurred") surface the outermost exception that propagated.

After the fix, re-parsing the same Phase 4 executed notebook (no re-dispatch — the saved `.ipynb` is the ground-truth fixture) yields:

```text
cell 3: ResumeRunNotFoundError: --resume: no rows in fusion_bundle_state for run_id='tc29b-not-a-real-id'. Check the value (operator typo?) or use `aidp-fusion-bundle status` to list recent run_ids.
```

— exactly the shape Step 8's enrichment path appends to `DispatchRunFailedError`'s message. The CLI catch at `commands/run.py:229` then renders this in the operator's red error block. Locked by 6 new unit tests in `tests/unit/dispatch/test_rest_client.py::TestExtractCellErrorsStderrStream`:

- canonical `output_type: error` path still extracted (regression lock)
- stderr-stream with single traceback → ename + evalue extracted
- stderr-stream with no traceback (e.g., Spark INFO noise) → ignored
- chained exception → outermost exception wins
- stdout stream → never extracted (defensive)
- `text` field as list → joined correctly

---

## Notes & boundaries

- **The custom Phase 2 notebook is a TC27-style monkeypatch** — there's no operator-facing hook in the productized CLI to inject a runtime failure into a specific silver builder. Phases 1, 3, and 4 use the productized `build_notebook` end-to-end so the resume_run_id `repr()`-injection path (fix5 Step 1) gets live exercise. Phase 2's custom notebook still emits the marker via the productized `summary.to_marker_dict()`, so the marker-parser exercise is faithful to what `build_notebook`'s run cell would produce on a real failed run.
- **Marker branch on Phase 2 is data-dependent**: when a real production run fails such that `repr(exc)` happens to contain no nested quote characters (e.g., a `KeyError('foo')` with a single-quoted argument), the JSON parse will succeed cleanly and the dispatcher returns a normal `RunSummary` (exit 1 with rendered failed-step table). The regex-fallback branch fires for the typical `RuntimeError("message")` / `ValueError("message")` / `RuntimeError(repr(...))` shapes — anything where `repr(exc)` embeds double quotes. TC29b Phase 2's induced `RuntimeError("…")` triggers the fallback by construction; production users will see one branch or the other depending on the cluster-side exception class.
- **Single-tenant evidence** — this captures the dispatch-layer plumbing claim against `fusion_bundle_dev`. Multi-tenant portability (P3.7 / P3.9) is tracked separately and is not in scope for fix5.
- **No `aidp-fusion-bundle status` CLI yet** — the remediation hint in the `ResumeRunNotFoundError` message refers to a future helper. Today operators query `fusion_bundle_state` directly.
- **Cluster auto-termination interaction**: between Phase 3 (12:14:14) and Phase 4 (12:14:20) the cluster stayed warm — no re-init pause. If running TC29b after a cluster STOP, the first phase will absorb ~30-60 s of warm-up; subsequent phases reuse the same context.
- **Cleanup** — Phase 2's monkeypatch is reverted at the end of the run cell, so the cluster-side process state is clean for any subsequent dispatch.

## Cross-references

- BACKLOG.md §P1.5ε-fix5 — implementation tracking entry; this evidence closes the live-evidence acceptance criterion.
- PR #19 against `craxelfn/claude-code-plugins` — `P1.5ε-fix5 — Resume via REST dispatch + marker-parse hardening + cell-error enrichment`.
- TC27 evidence file (`tests/live/TC27_resume_from_checkpoint_results.md`) — original `--inline` resume validation + the deferred marker-parse-fragility note that fix5 closes.
- TC29 evidence file (`tests/live/TC29_rest_dispatch.md`) — REST-dispatch baseline that fix5 builds on.
- `.claude/skills/fusion-tc26-run/tc29b_resume_dispatch.py` — the 4-phase orchestration script (modeled on `tc27_dispatch.py`).
- `scripts/oracle_ai_data_platform_fusion_bundle/dispatch/rest_client.py` — `parse_marker` regex fallback + `extract_cell_errors` stderr-stream support.
- `scripts/oracle_ai_data_platform_fusion_bundle/dispatch/errors.py` — `DispatchMarkerDegradedError` typed exception (stable code `DISPATCH_MARKER_DEGRADED`).
- `scripts/oracle_ai_data_platform_fusion_bundle/dispatch/__init__.py` — sentinel handling + cell-error enrichment integration points.
