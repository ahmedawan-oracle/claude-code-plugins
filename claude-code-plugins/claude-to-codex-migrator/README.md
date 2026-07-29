# Claude to Codex Migrator

`claude-to-codex-migrator` is a skills-only Claude Code plugin that converts Claude skills, plugins, commands, agents, and integration bundles into validated Codex Skills or plugins.

It inventories every source file, chooses the simplest compatible Codex architecture, performs deterministic transformations, scans the generated package for source-platform leftovers, and produces decision, migration, cleanup, and validation reports.

Published by [Oussama Lakrafi](https://github.com/craxelfn). Canonical home: [craxelfn/claude-to-codex-migrator](https://github.com/craxelfn/claude-to-codex-migrator).

## Requirements

- Claude Code with plugin support
- Git
- Python 3.10 or newer available as `python3`

The plugin has no MCP server, connector, authentication flow, or external runtime service. Migration runs locally against paths you explicitly provide.

## Install

Install from this marketplace:

```
/plugin marketplace add ahmedawan-oracle/claude-code-plugins
/plugin install claude-to-codex-migrator@oracle-ai-data-platform-workbench-suite
```

Start a new Claude Code session after installation so the bundled Skill is discovered.

## Use the plugin

Describe the migration naturally; the `claude-to-codex-migrator` skill is invoked automatically.

Examples:

```text
Migrate ./legacy-plugin into a clean Codex package.

Convert ./assistant-package.zip and put the result in ./migration-output.

Inspect this repository, decide Skill versus plugin, migrate it, and report unresolved items.

Audit ./generated-package for Claude-specific filenames, metadata, and instructions.
```

The migrator accepts:

- A local folder
- A ZIP archive
- A single file
- Pasted content
- A JSON multi-file bundle through standard input
- A locally installed source package
- A repository tree or implementation document supplied in the conversation

By default, instruction-driven sources become Codex Skills. Sources that require MCP, apps, authenticated tools, hooks, or runtime integration become Codex plugins.

## Generated output

A migration produces two separate trees:

```text
migration-output/
├── package/
│   └── <generated-skill-or-plugin>/
└── reports/
    ├── decision.md
    ├── source-inventory.json
    ├── migration-plan.json
    ├── migration-report.md
    ├── cleanup-report.json
    ├── validation-report.md
    └── unresolved/
```

Strict mode exits unsuccessfully when manual work or validation failures remain, while preserving the reports and unresolved source snapshots for review.

## Run the migration engine directly

You can run the deterministic engine without going through the skill:

```bash
python3 skills/claude-to-codex-migrator/scripts/migrate.py ./source-package \
  --out ./migration-output \
  --strict
```

Inventory a package without generating output:

```bash
python3 skills/claude-to-codex-migrator/scripts/inventory_source.py ./source-package
```

Validate or scan a generated package:

```bash
python3 skills/claude-to-codex-migrator/scripts/validate_output.py \
  ./migration-output/package/<name> \
  --target auto

python3 skills/claude-to-codex-migrator/scripts/scan_leftovers.py \
  ./migration-output/package/<name>
```

## Plugin layout

```text
.claude-plugin/plugin.json
skills/claude-to-codex-migrator/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── scripts/
tests/
```

## Development

Run the test suite from the plugin root:

```bash
python3 -m unittest -v
```

Before releasing a new version, update the semantic version in `.claude-plugin/plugin.json`, rerun the validators, and test installation from the marketplace source.

## Safety

- ZIP traversal, absolute archive paths, encrypted entries, symlinks, and archives exceeding size or entry-count limits are rejected.
- Existing output is not replaced unless `--force` is explicit, and even then only when the target is a single file, an effectively empty directory, or a directory whose every file is listed in the migration's own ownership manifest (`reports/output-manifest.json`).
- The output location must not overlap the source path in either direction.
- Executable code, scripts, dependency manifests, and Markdown code spans receive only mechanical rewrites (environment variables, well-known paths) — never prose substitutions that would corrupt imports or dependency pins. Remaining source terms there are flagged by the cleanup scan as manual work.
- Binary and oversized files are scanned at the byte level (ASCII and UTF-16) for source-platform terms, so they cannot bypass the cleanup scan.
- Version-bearing model identifiers and external URLs in file contents are never rewritten automatically; the cleanup scan flags any that still mention the source platform so they become explicit manual work. Generated package names and paths are always fully rewritten.
- Migrated hooks, MCP, and app configuration are quarantined under `reports/unresolved/` by default; `--trust-runtime` places them at active discovery paths only after review.
- Unknown components are reported and preserved outside the distributable package instead of being silently discarded.
- The migrator does not publish, install, or enable a generated package unless separately requested.

## License

[MIT](LICENSE).
