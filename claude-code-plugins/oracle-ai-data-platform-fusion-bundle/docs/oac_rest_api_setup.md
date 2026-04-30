# OAC REST API setup — automating `dashboard install`

The bundle's `aidp-fusion-bundle dashboard install --target oac` command can install the AIDP JDBC connection + import workbooks via OAC's public REST API at `/api/20210901/...`. This requires a one-time IDCS confidential application registration so the CLI can obtain OAuth Bearer tokens.

This is admin-level setup (15 min). After it's done, the bundle's CLI fully automates OAC connection install — no UI clicks needed.

> **If you don't have IDCS admin access**, skip this doc and use the UI walkthrough in [TC10_oac_integration_results.md](../tests/live/TC10_oac_integration_results.md) instead. Both produce the same result; only the automation level differs.

---

## Why a Bearer token is required

OAC's public REST API (`https://<oac-host>/api/20210901/...`) **only accepts** OAuth 2.0 Bearer tokens. The session cookies that drive OAC's web UI work for `/ui/sac/api/v1/...` and `/ui/dv/ui/api/v1/...` (internal UI APIs) but **not** for the documented `/api/20210901/...` endpoints. Live-verified 2026-04-30:

```
GET /api/20210901/catalog/connections
→ HTTP 401 Unauthorized
→ www-authenticate: Bearer error="invalid_session"
```

To obtain a Bearer for client-credentials flow (suitable for a CI/CD-style automation tool like our CLI), we need an IDCS confidential application registered in the OAC's IDCS domain.

---

## Prerequisites

| | |
|---|---|
| Permissions | **Identity Domain Administrator** in the OAC's IDCS domain (separate from OCI tenancy admin) |
| MFA | Many IDCS domains require MFA enrollment before admin console access. Enroll first. |
| OAC instance | Running, with at least one administrator user that can create connections via REST |

---

## One-time IDCS app registration

### Step 1 — Open IDCS admin console

URL pattern:
```
https://idcs-<your-idcs-stripe-id>.identity.oraclecloud.com/ui/v1/adminconsole
```

The `idcs-<stripe>` part is in the URL when you log into OAC. Examples:
- For `https://oacai.cealinfra.com/ui/`, the IDCS host turned out to be `idcs-f5e26b80ce5d4d20a66ba648b5e00403.identity.oraclecloud.com`

Log in with an **Identity Domain Administrator** account. (`Oacadmin1` in our test setup is an OAC admin; it may or may not have IDCS admin — verify by trying to load `/ui/v1/adminconsole`.)

### Step 2 — Create a confidential application

1. Navigate to **Applications** → **Add application**
2. Choose **Confidential Application**
3. Click **Launch workflow**
4. **App details**:
   - Name: `aidp-fusion-bundle-installer` (or any name you prefer)
   - Description: `OAuth client for the oracle-ai-data-platform-fusion-bundle CLI`
5. Click **Next**
6. **Configure OAuth**:
   - Choose **Configure this application as a client now**
   - **Allowed Grant Types**: check **Client Credentials**
   - **Add Resources** (the OAC REST scope): scroll to find your OAC instance under "Resources" — typical resource name is `Oracle Analytics Cloud` or similar; add the relevant **scope** (often `urn:opc:resource:fawcommon:OAC` or the scope your IDCS admin defines for OAC API access). If unclear, your OAC admin can confirm the exact scope.
7. Skip "Configure this application as a resource server" (we're a client only)
8. Click **Next** → **Skip and create** for the resource server step → **Finish**
9. **Activate** the application (use the "Activate" button on the app's page)

### Step 3 — Capture the credentials

On the activated app's page:
- **Client ID** — visible on the OAuth Configuration page
- **Client Secret** — click **Show Secret** to reveal once

Capture both. The bundle will use:
- `client_id` for the OAuth token request
- `client_secret` for the OAuth token request

### Step 4 — Store credentials in OCI Vault

Don't put the secret in `bundle.yaml` directly. Use Vault references:

```bash
oci --profile DEFAULT vault secret create-base64 \
  --compartment-id $COMPARTMENT_OCID \
  --secret-name "aidp-fusion-bundle-oac-client-secret" \
  --vault-id $VAULT_OCID \
  --key-id $KEY_OCID \
  --secret-content-content $(printf '%s' "$CLIENT_SECRET" | base64 -w0)
```

Capture the secret OCID and reference it in `bundle.yaml`:

```yaml
oac:
  enabled: true
  url: https://oacai.cealinfra.com
  oauthClientId: <client_id>
  oauthClientSecret: ${vault:ocid1.vaultsecret.oc1.iad...}
```

---

## Verify token acquisition

Once the app is registered + activated, fetch a Bearer token to confirm:

```bash
curl -s -X POST \
  "https://idcs-<stripe>.identity.oraclecloud.com/oauth2/v1/token" \
  -u "$CLIENT_ID:$CLIENT_SECRET" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&scope=urn:opc:resource:fawcommon:OAC"
```

Expect a JSON response with `access_token`. Use that token:

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "https://oacai.cealinfra.com/api/20210901/catalog/connections"
```

If you see `200 OK` with a JSON list of connections, the OAuth path works.

---

## What the bundle does with this

`aidp-fusion-bundle dashboard install --target oac --oac-url <url>` uses these credentials to:

1. Fetch a Bearer (`POST /oauth2/v1/token` against IDCS)
2. Build the AIDP connection JSON (the 6-key shape verified in [TC10_oac_integration_results.md](../tests/live/TC10_oac_integration_results.md))
3. Upload the API key PEM as a binary attachment
4. `POST /api/20210901/catalog/connections` to register the data source
5. (Optional) `POST /api/20210901/catalog/workbooks/{id}/imports` to import the bundle's `oac/workbooks/*.dva` files

All without UI clicks. End-state matches what the UI walkthrough produces.

---

## Known IDCS-admin-access gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `/ui/v1/adminconsole` redirects to MFA enrollment | IDCS domain enforces MFA on admin access | Enroll in MFA via mobile authenticator app (5 min one-time) |
| Can log into OAC but not IDCS admin console | OAC user doesn't have Identity Domain Admin role | Have your IDCS domain admin grant `Identity Domain Administrator` role |
| `Add resources` step shows no OAC option | OAC scope isn't published as an IDCS resource server | Your OAC admin needs to expose OAC's API scope first; this is environment-specific |
| Token acquisition returns `invalid_grant` | Confidential app not activated, or Client Credentials grant not enabled | Re-check Step 2.6 and Step 2.9 |

---

## Fallback path

If IDCS admin access isn't available (common in shared OAC tenancies), use the **UI install path** documented in [TC10_oac_integration_results.md](../tests/live/TC10_oac_integration_results.md):

1. Run `aidp-fusion-bundle dashboard install --target oac --oac-url <url> --print-only`  → outputs the JSON file the user uploads
2. Admin opens OAC → Data → Connections → Create → "Oracle AI Data Platform"
3. Uploads the printed JSON + the bundle-generated PEM
4. Saves

Both paths produce the same `aidp_fusion_jdbc` connection in OAC. The Bearer-token automation just removes the manual upload step for environments where IDCS admin is available.

---

## References

- [OAC REST API endpoints](https://docs.oracle.com/en/cloud/paas/analytics-cloud/acapi/rest-endpoints.html) — official endpoint list
- [IDCS confidential applications](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/usingoauthapps.htm) — Oracle docs
- Bundle's [TC10 results](../tests/live/TC10_oac_integration_results.md) — verified UI path, schema discovery, and what's already proven
- [OAC AIDP connector schema](../../../../../.claude/projects/c--Users-anuma-aidp/memory/project_oac_aidp_connector_schema.md) — the 6-key JSON file shape
