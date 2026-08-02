# Report schema

## Output tree

```text
migration-output/
├── package/
│   └── <target-name>/
└── reports/
    ├── decision.md
    ├── source-inventory.json
    ├── migration-plan.json
    ├── migration-report.md
    ├── cleanup-report.json
    ├── validation-report.json
    ├── validation-report.md
    └── unresolved/
```

Only `package/<target-name>/` is distributable.

## `source-inventory.json`

Contains:

- Staged source root
- Input kind
- Every inventoried file
- Component classification
- Size and SHA-256
- Binary flag
- Detected source-term categories
- Inventory warnings

The report omits file bodies to avoid unnecessary duplication.

## `migration-plan.json`

Top-level fields:

- `sourceRoot`
- `sourceKind`
- `targetName`
- `decision`
- `metadata`
- `items`
- `warnings`
- `summary`

Every item contains:

- `source_path`
- `kind`
- `operation`
- `target_path`
- `reason`
- `rewrites`
- `status`

Allowed operations:

- `keep-as-is`
- `rename`
- `rewrite`
- `split-into-reference`
- `delete`
- `manual`

## `cleanup-report.json`

Contains:

- `passed`
- `findingCount`
- `findings`

Each finding identifies the relative path, filename or content kind, pattern category, and line when available.

## `validation-report.json`

Contains:

- `target`
- `ok`
- `errors`
- `warnings`
- `cleanupFindings`

`ok` is false when structural errors or cleanup findings exist. Manual plan items are handled by strict mode and remain visible in the migration plan.

## Exit codes

- `0`: migration and requested strictness passed
- `1`: input, output, or argument failure
- `2`: strict migration produced reports but manual items or validation failures remain
