# Minimal Bundle Example

This directory is the narrow scaffold used by
`aidp-fusion-bundle init --template minimal-bundle`. It builds
`gold.supplier_spend` from the shipped `fusion-finance-starter` content pack.

Use it like this:

```bash
mkdir my-fusion-lake
cd my-fusion-lake
aidp-fusion-bundle init --template minimal-bundle
```

For the default full finance starter, omit `--template`:

```bash
aidp-fusion-bundle init
```

Then replace placeholders in:

- `bundle.yaml`
- `aidp.config.yaml`

Continue with:

```bash
aidp-fusion-bundle validate
aidp-fusion-bundle dashboard mcp-setup --connector-js <path-to-oac-mcp-connect.js>
aidp-fusion-bundle bootstrap --check-iam
aidp-fusion-bundle run --mode seed --dry-run
```

`bootstrap` creates `profiles/finance-default.yaml` beside `bundle.yaml`.
