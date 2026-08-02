# Component mapping

## Contents

- [Decision rule](#decision-rule)
- [Mapping matrix](#mapping-matrix)
- [Skills](#skills)
- [Commands](#commands)
- [Agents](#agents)
- [MCP and apps](#mcp-and-apps)
- [Hooks](#hooks)
- [Settings and LSP](#settings-and-lsp)
- [Unknown components](#unknown-components)

## Decision rule

Build as a Skill when the source is mainly instructions, prompts, documentation, content processing, or reusable workflow logic.

Build as a plugin when the package requires MCP, app mappings, authenticated tools, distributable lifecycle hooks, runtime code, or a stable multi-Skill integration bundle.

Keep Skills as the workflow authoring unit even when a plugin is the distribution target.

## Mapping matrix

| Source component | Skill target | Plugin target | Default operation |
| --- | --- | --- | --- |
| Source manifest | Consume as metadata | Generate `.codex-plugin/plugin.json` | Delete or rewrite |
| Existing Skill | Root Skill or reference | `skills/<name>/` | Rewrite |
| Markdown command | Workflow reference | Bundled Skill | Rewrite |
| Markdown agent | Workflow reference | Bundled Skill | Rewrite |
| MCP config | Manual | `.mcp.json` plus dependencies | Rewrite and validate |
| App mapping | Manual | `.app.json` | Rewrite and validate |
| Hook config | Manual | Default `hooks/hooks.json` | Rewrite and validate |
| Root scripts/runtime | Manual | Preserve when required | Rewrite or rename |
| References | `references/` | Closest consuming Skill | Rewrite or rename |
| Assets | `assets/` | Skill or plugin assets | Rename or keep |
| Settings/LSP | Manual | Manual | Preserve outside package |
| Tests/repository metadata | Preserve at original path | Preserve at original path | Rewrite or rename |
| CI workflows and actions (`.github/workflows/`, `.github/actions/`) | Manual until trusted | Manual until trusted | Rewrite only with `--trust-runtime` |
| Unknown files | Reference only if clearly textual guidance | Manual | Never silently drop |

## Skills

Normalize Skill names to lowercase hyphen-case. Keep only `name` and `description` in `SKILL.md` frontmatter. Put triggering language in `description`, because the body loads only after selection.

For a single instruction workflow, produce one standalone Skill. For several instruction workflows, produce one coordinating Skill and split individual workflows into direct `references/` files.

For plugin output, preserve one Skill directory per distinct workflow. Generate `agents/openai.yaml` for each Skill.

## Commands

Do not reproduce slash-command mechanics. Preserve the command's outcome and instructions as a Skill or reference. Parse frontmatter, keep the full body, rewrite platform-specific invocations, and include a clear trigger description.

Never use substring counts as the conversion decision. Inspect the actual command constructs and report unresolved runtime behavior.

## Agents

Treat source agents as specialized instruction workflows by default. Preserve the complete instruction body. Convert reusable behavior to a Skill or reference rather than assuming plugin-bundled custom-agent discovery.

Do not copy source model identifiers blindly. Use the active Codex model unless the user explicitly selects a valid target model. Record source tool assumptions and require the active session to expose equivalent capabilities.

Only emit standalone custom-agent TOML when the requested target is explicitly project- or user-scoped and the user asks for that configuration.

## MCP and apps

MCP or app behavior selects a plugin target unless the user requests plan-only output. Preserve the config only after validating:

- Server object shape
- Local commands and argument paths
- Required scripts and runtime files
- Environment variables
- Working-directory assumptions
- Authentication and external service requirements

Missing local executables are validation errors.

## Hooks

Current Codex documentation supports plugin-bundled hooks and default discovery at `hooks/hooks.json`. The installed plugin validator may reject a manifest-level `hooks` field, so place compatible hooks at the default path and omit the manifest field unless the selected target toolchain explicitly accepts it.

Rewrite source plugin environment variables to `PLUGIN_ROOT` and `PLUGIN_DATA`. Preserve the event and matcher only after confirming their Codex semantics. Never enable or trust migrated hooks automatically.

## Settings and LSP

Keep settings and LSP configuration outside the distributable package by default. Describe the required project or user configuration as a manual follow-up. Do not invent a package-level mapping.

## Unknown components

Classify every unknown path as `manual` unless it is clearly textual documentation that belongs in `references/`. Copy manual items to `reports/unresolved/` with their source-relative paths. Never place unknown executables into the package without understanding their consumers.
