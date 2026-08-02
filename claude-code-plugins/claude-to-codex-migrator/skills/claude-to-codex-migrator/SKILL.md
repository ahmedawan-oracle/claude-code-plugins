---
name: claude-to-codex-migrator
description: Migrate Claude skills, plugins, assistant packages, ZIP archives, pasted files, repository trees, or implementation documentation into clean Codex Skills or plugins. Use when Codex must inventory a source package, choose Skill versus plugin architecture, preserve behavior, rewrite platform-specific instructions, validate MCP or hooks, remove Claude branding from the distributable output, and produce migration and cleanup reports.
---

# Claude to Codex Migrator

Migrate source packages through a clean-room, report-backed workflow. Use the bundled scripts for deterministic staging, inventory, transformation, cleanup scanning, and validation. Apply judgment only where the reports identify semantic or unsupported work.

Resolve `<skill-root>` to the directory containing this `SKILL.md`. Invoke every bundled script by its path under `<skill-root>/scripts`; do not assume the user's current working directory is the installed Skill directory.

## Core rules

1. Preserve behavior and intent before optimizing structure.
2. Classify every source file as `keep-as-is`, `rename`, `rewrite`, `split-into-reference`, `delete`, or `manual`.
3. Build as a Skill for instructions, prompts, documentation, and reusable workflows.
4. Build as a plugin for MCP, apps, authenticated runtime behavior, distributable hooks, or mixed integration bundles.
5. Keep reports and unresolved source snapshots outside the distributable package.
6. Never silently drop an unknown component.
7. Finish only after the package passes structural validation and a filename/content cleanup scan.

## Workflow

### 1. Normalize the input

Accept a folder, ZIP, single file, or stdin bundle directly. For pasted multi-file content, repository trees, or documentation-only descriptions, materialize a JSON stdin bundle before migration.

When the user names an installed source package but does not provide its path, discover or resolve it first:

```bash
python3 <skill-root>/scripts/discover_installed.py
python3 <skill-root>/scripts/discover_installed.py --resolve <plugin-id>
```

Read [input-contract.md](references/input-contract.md) when the input is not a normal source folder.

Inventory without writing output:

```bash
python3 <skill-root>/scripts/inventory_source.py <source>
```

For stdin, pass either plain text or a JSON object whose `files` field maps relative paths to string contents.

### 2. Inspect and plan

Review the inventory for hidden files, runtime dependencies, source-specific references, unknown components, and warnings. Read [component-mapping.md](references/component-mapping.md) when commands, agents, hooks, MCP, apps, settings, LSP, or unknown files exist.

Use automatic architecture selection unless the user explicitly requires a target:

```bash
python3 <skill-root>/scripts/migrate.py <source> --out <output> --strict
```

Use `--target skill` or `--target plugin` only when the user or verified target architecture requires it. Use `--name <name>` to override the normalized package name. Use `--force` only when replacing the chosen output is intentional.

Hooks, MCP, and app configuration are quarantined as manual items by default and preserved under `reports/unresolved/`. Review every hook command and MCP executable there, then re-run with `--trust-runtime` to place them at their active discovery paths. Never pass `--trust-runtime` on the first run of an unreviewed source.

### 3. Resolve semantic work

Inspect these files after every run:

- `reports/decision.md`
- `reports/migration-plan.json`
- `reports/migration-report.md`
- `reports/cleanup-report.json`
- `reports/validation-report.md`
- `reports/unresolved/` when present

Read [transformation-rules.md](references/transformation-rules.md) before resolving manual items or editing generated instructions. Preserve the full instruction body of source agents and commands. Do not copy source model identifiers blindly. Verify hook events, matcher behavior, local executable paths, environment variables, and MCP dependencies.

If strict mode exits with code 2, keep the generated reports, resolve only the identified issues, and rerun or validate the corrected package. Do not report completion while manual items or validation failures remain unless the user asked for a plan-only migration.

### 4. Validate the package

Run the target-aware validator after any manual edit:

```bash
python3 <skill-root>/scripts/validate_output.py <output>/package/<name> --target auto
```

Run the cleanup scanner independently:

```bash
python3 <skill-root>/scripts/scan_leftovers.py <output>/package/<name>
```

For generated plugins, also run the current `plugin-creator` validator when available. For generated Skills, run the current `skill-creator` validator when available. Read [validation-checklist.md](references/validation-checklist.md) before handoff.

### 5. Report the result

Return, in order:

1. `build as Skill` or `build as plugin`
2. Recommended Codex architecture
3. Migration plan summary
4. Generated package tree or package path
5. Cleanup report summary
6. Validation result
7. Unresolved issues or manual follow-ups

Read [report-schema.md](references/report-schema.md) when another tool will consume the JSON reports.

## Input examples

Folder or ZIP:

```bash
python3 <skill-root>/scripts/migrate.py ./source-package --out ./migration-output --strict
python3 <skill-root>/scripts/migrate.py ./source-package.zip --out ./migration-output --strict
```

Plain pasted documentation:

```bash
printf '%s' '<pasted content>' | python3 <skill-root>/scripts/migrate.py - --stdin-name SOURCE.md --out ./migration-output --strict
```

Multi-file bundle:

```json
{
  "files": {
    ".claude-plugin/plugin.json": "{\"name\":\"example\",\"version\":\"1.0.0\"}",
    "commands/review.md": "# Review\n\nReview the current change."
  }
}
```

## Safety boundaries

- Reject ZIP traversal, absolute archive paths, encrypted entries, and symlinks.
- Refuse to replace existing output unless `--force` is explicit, and only when every file is listed in the output's ownership manifest.
- Refuse output locations that overlap the source path.
- Treat hook commands and MCP executables as untrusted until reviewed; they stay quarantined unless `--trust-runtime` is passed after review.
- Never apply prose rewrites to executable code or dependency manifests; only environment variables and well-known paths are rewritten there.
- Keep unresolved original files under `reports/unresolved/`, never inside the package.
- Do not install, publish, enable hooks, or modify marketplaces unless the user separately requests that action.
