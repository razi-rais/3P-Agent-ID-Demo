# Post-deploy manual steps

Two steps cannot be completed before the Container App exists, and two steps cannot be done via `az ad app update`. Run them after Step 6 (deploy) in the main SKILL procedure.

## 1. Add the production SPA redirect URI

The Client SPA app was registered with only `http://localhost:3003`. The production `https://<APP_FQDN>` must be added, or the browser MSAL popup fails with `AADSTS50011`.

`az ad app update --web-redirect-uris` does NOT modify SPA URIs — you must PATCH Graph directly:

```bash
bash scripts/add-spa-redirect-uri.sh
```

This fetches existing `spa.redirectUris`, appends `https://$APP_FQDN`, PATCHes back. Idempotent.

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
pwsh -NoProfile -File scripts/grant-agent-obo-consent.ps1 -AgentAppId "$AGENT_CLIENT_ID" -TenantId "$TENANT_ID"
```

This creates an `oauth2PermissionGrant`: `clientId=<Agent SP>`, `resourceId=<Graph SP>`, `consentType=AllPrincipals`, `scope=User.Read`. Idempotent — checks for existing grant first.

## Why these aren't automated inside Step 6

- **SPA URI** depends on `APP_FQDN`, which exists only after `az containerapp create`. Cannot be precomputed.
- **OBO consent** is intentionally separate because autonomous-only deployments don't need it. Bundling it would hide a tenant-level admin consent behind a generic deploy command.
