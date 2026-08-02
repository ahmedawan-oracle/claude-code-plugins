# Input contract

## Contents

- [Supported inputs](#supported-inputs)
- [Folder input](#folder-input)
- [ZIP input](#zip-input)
- [Single-file and stdin input](#single-file-and-stdin-input)
- [Pasted multi-file bundles](#pasted-multi-file-bundles)
- [Repository trees](#repository-trees)
- [Documentation-only sources](#documentation-only-sources)
- [Staging safety](#staging-safety)

## Supported inputs

The deterministic scripts accept:

- A local folder
- A ZIP archive
- A single local file
- Plain text from stdin
- A JSON multi-file bundle from stdin
- A locally installed package resolved through the discovery helper

The Skill can also materialize repository-tree descriptions or implementation documentation into the stdin bundle format.

Resolve an installed source package with:

```bash
python3 <skill-root>/scripts/discover_installed.py --resolve <plugin-id>
```

## Folder input

Pass the source root directly:

```bash
python3 <skill-root>/scripts/migrate.py ./source --out ./migration-output --strict
```

The staging layer copies hidden files and ordinary files into an isolated workspace. It excludes `.git`, `node_modules`, and `__pycache__` because they are repository or generated state rather than package source.

The staging layer rejects symlinked files and directories. Resolve required links into normal files before migration.

## ZIP input

Pass the archive path directly. The staging layer:

- Rejects absolute paths
- Rejects `..` traversal
- Rejects symlink entries
- Rejects encrypted entries
- Recognizes a single wrapped package directory

Do not manually extract an untrusted ZIP to bypass these checks.

## Single-file and stdin input

Pass a single file directly or use `-` for stdin:

```bash
python3 <skill-root>/scripts/migrate.py ./assistant.md --out ./migration-output
printf '%s' '<content>' | python3 <skill-root>/scripts/migrate.py - --stdin-name SOURCE.md --out ./migration-output
```

Use a meaningful `--stdin-name` extension so the planner can classify the content.

## Pasted multi-file bundles

Materialize pasted files as JSON:

```json
{
  "files": {
    "relative/path.md": "file contents",
    "config/settings.json": "{\"enabled\":true}"
  }
}
```

Every key must be a safe relative path. Every value must be a string. Pipe the JSON to `<skill-root>/scripts/migrate.py -`.

## Repository trees

When the user provides only a tree:

1. Ask for or retrieve the contents required to preserve behavior.
2. Materialize every available file in a JSON bundle.
3. Represent unavailable files as documentation notes rather than inventing contents.
4. Mark missing behavioral dependencies as manual follow-ups.

A filename-only tree is sufficient for a migration plan, not a completed behavior-preserving migration.

## Documentation-only sources

When documentation describes the implementation but no package exists:

1. Save the documentation as `SOURCE.md`.
2. Add any explicit schemas or prompts as separate bundle files.
3. Run automatic architecture selection.
4. Treat inferred behavior as an assumption in the report.
5. Avoid inventing runtime integrations not described by the source.

## Staging safety

The output must be separate from the source. The engine builds in a temporary clean room and atomically hands off a `package/` and `reports/` tree. Existing output requires explicit force replacement.
