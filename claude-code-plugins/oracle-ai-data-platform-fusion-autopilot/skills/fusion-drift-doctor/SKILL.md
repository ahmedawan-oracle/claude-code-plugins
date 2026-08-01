---
name: fusion-drift-doctor
description: "Diagnose and route Fusion->AIDP schema-drift / gate failures. When a run fails with a drift gate (AIDPF-2072 Fusion PVO drift, AIDPF-4070/4071 source-schema, AIDPF-2012 bronze fingerprint, AIDPF-4040 plan-hash), this read-only skill explains the code, probes the LIVE Fusion PVO, classifies each affected column, and routes the fix: bootstrap --refresh (mechanical re-pin), /medallion-author (overlay a new column name the pack never anticipated), re-seed (plan-hash drift), or human investigate. Use when an incremental/seed run reports schema drift or an AIDPF-20xx/40xx gate, when 'the Fusion PVO column changed', or as a precheck before /aidp-fusion-incremental. Does NOT fix anything itself — it diagnoses and hands off."
allowed-tools: Read, Bash, Glob, Grep
---

# fusion-drift-doctor — what drifted, and how to fix it

The runtime gates DETECT drift and fail the run; they don't tell you *which fix*.
This skill closes that: read the failure, probe the **live Fusion PVO**, classify
each affected column, and route to the right remediation. **Read-only** — it
diagnoses and hands off; it never edits packs/profiles or runs the pipeline.

## When to use
- A `seed`/`incremental` run failed with `AIDPF-2072` / `4070` / `4071` /
  `2012` / `4040`, or the summary says "schema drift" / "PVO drift".
- "The Fusion PVO column got renamed", "bronze schema changed", "why does my
  incremental keep failing the gate?"
- As a **precheck inside `/aidp-fusion-incremental`** before dispatch.

## The gate codes it triages

| Code | What it means | Route |
|---|---|---|
| **AIDPF-2072** | Live Fusion PVO schema drifted vs the bootstrap-pinned snapshot (a source column renamed/removed). | classify → `bootstrap --refresh` or `/medallion-author` |
| **AIDPF-4071** | A declared `outputSchema` column is **missing** from the live PVO. | classify (same as 2072) |
| **AIDPF-4070** | A live PVO column's **type changed** vs declared. | **`/medallion-author`** — draft a bronze type-overlay (retype `outputSchema` to the live type) from the persisted `AIDPF-4070__<node>.json` |
| **AIDPF-2012** | Bronze **table** fingerprint drifted vs the pinned profile fingerprint. | confirm the change is legit → `bootstrap --refresh` to re-pin |
| **AIDPF-4040** | **Plan-hash drift** — the node's plan-hash differs from the seed-pinned one. | classify the *cause* (below) — **re-seed only fixes a genuine plan-shape change** |

> Note the distinction: **PVO source drift (2072/4070/4071)** is detected by a
> metadata-only BICC probe of the live PVO — it catches a renamed source column
> **even when the bronze Delta table still holds the old one**. **Bronze-table
> drift (2012)** is a `DESCRIBE` of the materialized table. Different layers.

## Helper

| File | Role | Invoked via |
|---|---|---|
| `drift_classify.py` | Classify each columnAlias against the **live PVO** columns → `present` / `renamed_resolvable` (→ bootstrap --refresh) / `needs_overlay` (→ medallion-author) / `missing_literal` (→ investigate if it should exist, OR — if *legitimately absent* for this tenant — relax the `requiredColumns` assertion via a `relaxRequiredColumns` overlay through `/medallion-author`; AIDPF-2062/2063 guard it). | `Bash`, JSON in/out |

## Workflow
1. **Get the failure context.** Either read the failed run's diagnostic artifact
   (`.aidp/diagnostics/<run_id>/AIDPF-2072.json`, etc. — it carries the drifted
   columns), or note the AIDPF code from the run summary. For AIDPF-4040 / 2012
   the route is decided by the code (table above) — skip to step 4.
2. **Probe the live PVO (evidence).** For PVO drift (2072/4071), get the live PVO
   columns for the affected bronze sources — metadata-only, no row pull:
   ```bash
   aidp-fusion-bundle catalog probe-pvo <id> --datastore <PVO> --bicc-schema <Financial|HCM|SCM> ...
   ```
   (or reuse the diagnostic's observed columns). Gather the pack's
   `columnAliases` (`content-pack info <pack> --json`) + the profile's `resolved`
   picks for each affected alias.
3. **Classify** — shape `{live, aliases[, required_literals]}` and run:
   ```bash
   python3 skills/fusion-drift-doctor/drift_classify.py --input drift.json
   ```
   → per-alias `status` + `routes`.
4. **Route + recommend (don't fix):**
   - **`renamed_resolvable`** → tell the operator to run `aidp-fusion-bundle
     bootstrap --refresh` — a declared candidate still matches; refresh re-pins
     the alias to it.
   - **`needs_overlay`** → route to **`/medallion-author`**: a new column name
     the pack never anticipated; draft an overlay extending
     `columnAliases.<name>.candidates` with the observed live column (surfaced in
     `liveColumns`).
   - **`missing_literal`** → **investigate**: a declared literal column vanished
     — a pack/source mismatch a human resolves (no mechanical fix).
   - **AIDPF-4070 (type change)** → **`/medallion-author`** (bronze type-overlay):
     a declared bronze `outputSchema` type differs from the live PVO. The fix is a
     non-destructive **type-overlay** — a `bronze/<node>` `outputSchema` block
     override (or a same-id bronze file) retyping the column to the live type. The
     run persists an `AIDPF-4070__<node>.json` diagnostic that `/medallion-author`
     drafts the overlay from. (Identity fields stay off-limits — a PVO/key/target
     change is still a new node id.)
   - **AIDPF-4040** → identify *which* of two causes before recommending a fix.
     (Row-grain MERGE nodes increment cleanly after a seed as of 2026-06-15 —
     the plan-hash is mode-normalized, `LIMITS.md` P-incr-L1 resolved — so the
     watermark predicate rendering `1=1` (seed) vs `<col> > :watermark_src`
     (incremental) no longer false-trips 4040. A 4040 today means a *real*
     plan-shape change, not the mode you ran.)
     1. **Genuine plan-shape change** (you edited the node SQL / `outputSchema` /
        variation pick / tenant profile / watermark column since the seed) → if
        the edit was **unintended**, revert it. If **intended**, either re-pin
        with a **scoped** re-seed of just that node's layer: `run --mode seed
        --datasets <node> --layers <layer>` (runs over existing bronze/silver —
        does NOT re-extract bronze or touch other marts), **or** pass the hidden
        `run --mode incremental --repin-plan-hash` to repin the new hash and
        proceed *without* a full rebuild (writes a `mode='plan_hash_repin'` audit
        row to `fusion_bundle_state` — dev/sandbox only, never in SOX/production).
        `--repin-plan-hash` is the cheap path when you know the change was
        deliberate and want a true delta-MERGE on the very next run.
     2. **Stale pin from a pre-2026-06-15 build** → before the `_build_hash_input`
        param-exclusion fix, the hash baked in the per-run `run_id`, so 4040 fired
        on *every* content-pack incremental and re-seeding never helped. If the
        cluster wheel predates the fix, rebuild/redeploy, then re-seed once.
   - **AIDPF-2012** → after confirming the bronze change is intended,
     `bootstrap --refresh` re-pins the fingerprint (break-glass
     `--force-fingerprint-skip` only for dev).
5. **After the fix lands + a re-seed**, re-run `/aidp-fusion-incremental`.

## Skill family
- **Consumed by `/aidp-fusion-incremental`** (drift precheck) and usable after any
  seed/incremental failure; `aidp-fusion-status` and `aidp-fusion-autopilot` can
  route here on a gate failure.
- **Routes to** `bootstrap --refresh` (mechanical re-pin) and **`/medallion-author`**
  (Tier-2 overlay for a column the pack didn't anticipate).

## Safety invariants
- **Read-only** — never edits packs/profiles, never runs seed/incremental.
- **Live PVO is the evidence** for source drift (not the bronze table, not pack
  YAML); a renamed PVO column is caught even when bronze still has the old one.
- Never recommend `--force-fingerprint-skip` outside dev/break-glass.
