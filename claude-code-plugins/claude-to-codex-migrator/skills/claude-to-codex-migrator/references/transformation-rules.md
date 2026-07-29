# Transformation rules

## Contents

- [Behavior preservation](#behavior-preservation)
- [Naming](#naming)
- [Instruction rewriting](#instruction-rewriting)
- [Resource placement](#resource-placement)
- [Source-term cleanup](#source-term-cleanup)
- [Deletion](#deletion)
- [Manual items](#manual-items)

## Behavior preservation

Write a short behavior summary before changing a complex agent, command, hook, or integration. Compare the generated behavior with that summary after transformation.

Preserve:

- User-visible outcome
- Preconditions
- Ordering requirements
- Safety and approval boundaries
- Tool or data dependencies
- Error handling
- Stop conditions
- Required output shape

Do not replace specialized instructions with generic boilerplate.

## Naming

Use lowercase letters, digits, and hyphens for Skill and plugin identifiers. Match standalone Skill folder names to Skill frontmatter names. Match plugin folder names to manifest names.

Maintain a source-to-target path map. Rewrite every internal reference after renaming. Resolve collisions deterministically with numeric suffixes.

Do not lowercase arbitrary case-sensitive runtime filenames unless required. Remove or rewrite source branding in every output path.

## Instruction rewriting

Use imperative instructions. Keep trigger conditions in the Skill description. Strip unsupported frontmatter while preserving its meaning in instructions or manual notes.

For commands:

1. Remove slash-command invocation mechanics.
2. Preserve the requested outcome.
3. Rewrite source-agent or source-tool calls into Codex workflow instructions.

For agents:

1. Preserve the complete body.
2. Preserve role, priorities, constraints, and output rules.
3. Record tool assumptions.
4. Default to the active Codex model.

For documentation:

1. Remove source-only installation steps when they do not apply.
2. In a plugin target, keep documentation at its original repository path (`docs/`, root `README.md`); do not funnel it into one skill's references folder.
3. In a skill target, keep behavioral knowledge as references.
4. Do not duplicate the same rule in `SKILL.md` and a reference.

## Resource placement

Use:

- `scripts/` for deterministic or fragile executable steps
- `references/` for detailed instructions, mappings, and schemas loaded on demand
- `assets/` only for files consumed in generated output
- `skills/<name>/` for each plugin-bundled workflow
- `hooks/hooks.json` for compatible plugin hooks
- `.mcp.json` and `.app.json` only when their integration is present

Keep reports, source snapshots, and migration commentary outside the package. Source tests migrate with the package at their original relative paths so the migrated code keeps its regression coverage. Inert repository metadata (ignore files, editor config, issue templates, CODEOWNERS) also migrates in place, but CI workflows and composite actions under `.github/workflows/` and `.github/actions/` execute automatically once the repository is pushed — they are quarantined as manual items unless `--trust-runtime` is supplied after review.

## Source-term cleanup

Rewrite source product, vendor, directory, environment-variable, tool-command, documentation-link, and prompt-format references. Scan both paths and textual contents case-insensitively.

Use functional target terms rather than blind branding swaps when meaning differs. For example:

- Rewrite source plugin-root variables to `PLUGIN_ROOT`.
- Rewrite source model selection to active Codex model guidance.
- Rewrite plugin reload commands to current Codex restart or new-task guidance.
- Remove source marketplace metadata from a single-package target.

Binary contents receive path-only scanning. If binary metadata may contain source branding, flag it for manual inspection.

## Deletion

Delete only when the plan records why the component is unnecessary in the target. Typical deletions include source manifests consumed as metadata, generated caches, and obsolete installation documentation. Source tests and repository metadata are preserved, not deleted.

Do not classify unsupported behavior as deletion.

## Manual items

Use `manual` when behavior or placement cannot be determined safely. Include:

- Exact source path
- Concrete reason
- Required decision or compatibility check
- Original source snapshot under `reports/unresolved/`

A strict migration is incomplete while manual items remain.
