# Graph API Call Sequence

The end-to-end setup touches these endpoints, in order.

## Phase 1 — Blueprint + Agent Identity (`Start-EntraAgentIDWorkflow`)

| # | Method | Endpoint | Purpose |
|---|--------|----------|---------|
| 1 | GET | `/v1.0/me` | Identify sponsor for blueprint |
| 2 | POST | `/beta/applications/` (`@odata.type: AgentIdentityBlueprint`) | Create blueprint app |
| 3 | POST | `/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal` | Create blueprint SP |
| 4 | GET | `/beta/applications?$filter=appId eq '...'` | Get blueprint object ID |
| 5 | POST | `/beta/applications/<id>/addPassword` | Issue client secret |
| 6 | POST | Token endpoint (client_credentials) | Verify secret (T1) |
| 7 | POST | `/beta/serviceprincipals/graph.agentIdentity` | Create Agent Identity |
| 8 | POST | Token endpoint (client_credentials + parent assertion) | Exchange T1 → TR |
| 9 | GET | `/v1.0/servicePrincipals?$filter=appId eq '<graph>'` | Find Microsoft Graph SP |
| 10 | GET | `/v1.0/servicePrincipals/<agent-id>` | Verify agent SP exists |
| 11 | POST | `/v1.0/servicePrincipals/<graph-id>/appRoleAssignments` | Assign Graph app role to agent |

## Phase 2 — OBO Setup (`setup-obo-client-app` + `setup-obo-blueprint`)

| # | Method | Endpoint | Purpose |
|---|--------|----------|---------|
| 12 | POST | `/v1.0/applications` (via `az ad app create`) | Create Client SPA |
| 13 | PATCH | `/v1.0/applications/<spa>/` (set `spa.redirectUris`) | Configure redirect URI |
| 14 | POST | `/v1.0/servicePrincipals` | Create SPA SP |
| 15 | PATCH | `/beta/applications/<blueprint>` (`identifierUris`) | Set `api://<id>` |
| 16 | PATCH | `/beta/applications/<blueprint>` (`oauth2PermissionScopes`) | Add `access_as_user` |
| 17 | PATCH | `/v1.0/applications/<spa>` (`requiredResourceAccess`) | Bind SPA to blueprint scope |
| 18 | POST | `/v1.0/oauth2PermissionGrants` | Agent → Graph (User.Read etc.) |
| 19 | POST | `/v1.0/oauth2PermissionGrants` | Client SPA → Blueprint (access_as_user) |

## Runtime — Autonomous Flow (per request)

| # | Method | Endpoint | Purpose |
|---|--------|----------|---------|
| R1 | GET | `http://sidecar:5000/AuthorizationHeaderUnauthenticated/graph-app?AgentIdentity=<agent-id>` | Agent → sidecar asks for TR |
| R2 | POST | `https://login.microsoftonline.com/<tid>/oauth2/v2.0/token` (client_credentials) | Sidecar T1 → TR |
| R3 | GET | `https://graph.microsoft.com/v1.0/...` | Agent calls Graph with TR |

## Runtime — OBO Flow

| # | Method | Endpoint | Purpose |
|---|--------|----------|---------|
| O1 | Browser | MSAL sign-in popup → `/authorize` | User obtains Tc (aud=`api://<blueprint>`) |
| O2 | POST | `/api/chat` (agent) with `user_token: Tc` | Agent relays |
| O3 | GET | `http://sidecar:5000/AuthorizationHeader/graph?AgentIdentity=<agent-id>&Authorization=Bearer <Tc>` | OBO exchange |
| O4 | POST | Token endpoint (grant=on_behalf_of) | Sidecar T1 + Tc → TR (user context) |
| O5 | GET | `https://graph.microsoft.com/v1.0/...` | Agent calls Graph on behalf of user |
