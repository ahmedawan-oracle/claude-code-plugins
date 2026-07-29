# Validation checklist

## Structural completeness

- [ ] Every source file has exactly one migration operation.
- [ ] Every non-delete, non-manual plan target exists.
- [ ] Unknown components are preserved under `reports/unresolved/`.
- [ ] Reports are outside the package.
- [ ] The package was built in a clean staging directory.

## Skill validation

- [ ] `SKILL.md` exists.
- [ ] Frontmatter contains only `name` and `description`.
- [ ] Name uses lowercase hyphen-case.
- [ ] Folder name matches the Skill name.
- [ ] Description states what the Skill does and when it triggers.
- [ ] Body is non-empty and uses imperative instructions.
- [ ] `agents/openai.yaml` is present and its default prompt mentions `$<skill-name>`.
- [ ] Local Markdown links resolve inside the Skill.

## Plugin validation

- [ ] `.codex-plugin/plugin.json` exists.
- [ ] Plugin folder matches manifest name.
- [ ] Version is strict semver.
- [ ] Author and required interface metadata are complete.
- [ ] Component paths use `./` and exist.
- [ ] Every bundled Skill passes Skill validation.
- [ ] `.mcp.json` has an `mcpServers` object when present.
- [ ] MCP local commands and argument paths exist.
- [ ] `.app.json` exists when declared.
- [ ] Compatible hooks use `hooks/hooks.json` default discovery.
- [ ] Hook commands use target plugin environment variables.

## Behavior preservation

- [ ] Agent and command instruction bodies are preserved.
- [ ] Role priorities, constraints, output rules, and stop conditions remain.
- [ ] Source model identifiers are not copied blindly.
- [ ] Tool assumptions are mapped or reported.
- [ ] Runtime dependencies and authentication requirements are documented.
- [ ] Unsupported behavior remains a manual item rather than being deleted.

## Cleanup

- [ ] No source product names remain in package paths.
- [ ] No source product names remain in textual contents.
- [ ] No source vendor names remain.
- [ ] No source plugin directories remain.
- [ ] No source environment variables remain.
- [ ] No obsolete source commands or documentation URLs remain.
- [ ] Binary assets with possible embedded branding were reviewed.

## Final commands

```bash
python3 <skill-root>/scripts/validate_output.py <package-root> --target auto
python3 <skill-root>/scripts/scan_leftovers.py <package-root>
```

Also run the current Skill or plugin creator validator when available. Do not hand off a strict migration until both commands exit successfully and no manual items remain.
