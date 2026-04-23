# Post-deploy manual steps

Two steps cannot be completed before the Container App exists, and two cannot be done via `az ad app update`. Run them after Step 5 (deploy) in the main SKILL procedure.

## 1. Add the production SPA redirect URI

The Client SPA app was registered with only `http://localhost:3003`. The production `https://<APP_FQDN>` must be added, or the browser MSAL popup fails with `AADSTS50011`.

`az ad app update --web-redirect-uris` does NOT modify SPA URIs — you must PATCH Graph directly:

```bash
bash .github/skills/deploy-agent-aca-dev/scripts/add-spa-redirect-uri.sh
```

Idempotent — fetches existing `spa.redirectUris`, appends `https://$APP_FQDN`, PATCHes back.

Portal fallback: **Microsoft Entra ID** → **App registrations** → *Client SPA* → **Authentication** → **Single-page application** → **Add URI** → save.

## 2. Grant Agent → Graph delegated `User.Read` admin consent

### Symptom

Browser OBO flow fails with:

```
AADSTS65001: The user or administrator has not consented to use the application
```

### Why

`Start-EntraAgentIDWorkflow` grants **application** Graph permissions (e.g., `User.Read.All`) only. OBO additionally requires a **delegated** permission (`User.Read`) with admin consent at the tenant level, because the exchange happens on behalf of a user.

### Fix

```powershell
pwsh -NoProfile -File .github/skills/deploy-agent-aca-dev/scripts/grant-agent-obo-consent.ps1 \
  -AgentAppId "$AGENT_CLIENT_ID" -TenantId "$TENANT_ID"
```

Idempotent — checks for existing grant first. Creates `oauth2PermissionGrant`: `clientId=<Agent SP>`, `resourceId=<Graph SP>`, `consentType=AllPrincipals`, `scope=User.Read`.

## Why these aren't automated inside Step 5

- **SPA URI** depends on `APP_FQDN`, which exists only after `az containerapp create`. Cannot be precomputed.
- **OBO consent** is intentionally separate because autonomous-only deployments don't need it. Bundling it would hide a tenant-level admin consent behind a generic deploy command.

Both scripts are **identical** to the AWS skill's equivalents — these are Entra-level / Graph-level operations, not cloud-specific.
