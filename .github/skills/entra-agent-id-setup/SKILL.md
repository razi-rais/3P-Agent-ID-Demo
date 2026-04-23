---
name: entra-agent-id-setup
description: 'AI-guided setup for Microsoft Entra Agent ID. Use when the user wants to provision an Agent Identity Blueprint, Agent Identity, and OBO client app in an Entra tenant; when configuring the dev/AWS/GCP sidecar stack; when debugging 403 Authorization_RequestDenied on blueprint creation; when checking which Entra role is required (Global Admin / Agent ID Administrator / Agent ID Developer); when running Start-EntraAgentIDWorkflow / setup-obo-client-app / setup-obo-blueprint.'
---

# Entra Agent ID Setup (AI-Guided)

End-to-end provisioning of a Microsoft Entra Agent Identity + OBO-capable client app, plus bringing up the sidecar stack.

## When to Use

- User says: "set up agent ID", "create blueprint", "provision agent identity", "configure OBO", "set up the sidecar"
- User hits `403 Authorization_RequestDenied` when creating a blueprint
- User asks what Entra role is needed for agent ID
- User wants to go from empty tenant → working autonomous + OBO flows

## Prerequisites (verify BEFORE running scripts)

1. **Entra role** assigned to the signing-in user. One of:
   - `Global Administrator`
   - `Agent ID Administrator` (template `db506228-d27e-4b7d-95e5-295956d6615f`)
   - `Agent ID Developer` (template `adb2368d-a9be-41b5-8667-d96778e081b0`)
   - `Application Administrator` and `Cloud Application Administrator` are **NOT sufficient** — they cannot create `agentIdentityBlueprints` (verified: 403 on `POST /beta/applications/` with `@odata.type: Microsoft.Graph.AgentIdentityBlueprint`). See [references/permissions.md](./references/permissions.md).
2. **Tenant features**: Agent Identity APIs enabled. `AgentUser` entity type is feature-gated per-tenant — not required for autonomous/OBO flows; removed from our scripts. See [references/troubleshooting.md](./references/troubleshooting.md).
3. **Tooling**:
   - Azure CLI (`az`) signed in to the target tenant
   - PowerShell 7+ (`pwsh`)
   - `Microsoft.Graph.Authentication` and `Microsoft.Graph.Beta.Applications` PowerShell modules (install: `Install-Module Microsoft.Graph.Authentication, Microsoft.Graph.Beta.Applications -Scope CurrentUser`)
   - Docker Desktop (for sidecar)
4. **MFA**: If the signing-in user was just created, they MUST complete MFA enrollment via browser first (`AADSTS50079`). Password-only `az login -u -p` will fail.

## Procedure

### Step 1 — Create Blueprint + Agent Identity

Run the end-to-end workflow. Creates Blueprint app, Blueprint SP, client secret, Agent Identity, and assigns Graph permissions.

> **[!WARNING] Run in an interactive `pwsh` session, not a subshell.**
> `pwsh -Command "Connect-MgGraph ..."` spawns a child process where the WAM / browser popup may be hidden behind other windows and time out with `InteractiveBrowserCredential authentication failed: User canceled authentication.` Run `pwsh` first, then dot-source and connect inside that session.

```powershell
pwsh
. /path/to/repo/scripts/EntraAgentID-Functions.ps1
Connect-MgGraph -Scopes "AgentIdentityBlueprint.AddRemoveCreds.All","AgentIdentityBlueprint.Create","AgentIdentityBlueprint.DeleteRestore.All","AgentIdentity.DeleteRestore.All","DelegatedPermissionGrant.ReadWrite.All","Application.Read.All","AgentIdentityBlueprintPrincipal.Create","AppRoleAssignment.ReadWrite.All","Directory.Read.All","User.Read" -TenantId <tenant-id>

$r = Start-EntraAgentIDWorkflow -BlueprintName "Demo Blueprint" -AgentName "Weather Agent" -Permissions @("User.Read.All")
```

> **Scope list must match `EntraAgentID-Functions.ps1`'s internal check.** If you omit `AgentIdentityBlueprint.DeleteRestore.All` or `AgentIdentity.DeleteRestore.All`, the workflow throws `Missing required Microsoft Graph scopes` and prints the full reconnect command.

Write `$r.Blueprint.BlueprintAppId`, `$r.Blueprint.ClientSecret`, and `$r.Agent.AgentIdentityAppId` into `sidecar/dev/.env` as `BLUEPRINT_APP_ID`, `BLUEPRINT_CLIENT_SECRET`, `AGENT_CLIENT_ID`, plus `TENANT_ID`.

Supporting script: [EntraAgentID-Functions.ps1](./scripts/EntraAgentID-Functions.ps1)

### Step 2 — Register OBO Client SPA

```powershell
pwsh /path/to/repo/scripts/setup-obo-client-app.ps1
```

Creates an Entra app with SPA redirect URI `http://localhost:3003` and appends `CLIENT_SPA_APP_ID=<id>` to the detected `.env`.

Supporting script: [setup-obo-client-app.ps1](./scripts/setup-obo-client-app.ps1) (bash: [setup-obo-client-app.sh](./scripts/setup-obo-client-app.sh))

### Step 3 — Configure Blueprint for OBO

```powershell
pwsh /path/to/repo/scripts/setup-obo-blueprint.ps1 `
    -BlueprintAppId <blueprint-id> `
    -AgentAppId <agent-id> `
    -ClientSpaAppId <spa-id> `
    -TenantId <tenant-id>
```

This sets `api://<blueprint>` as App ID URI, adds the `access_as_user` delegated scope, adds API permission on the Client SPA, and grants admin consent for both `Agent→Graph` (User.Read) and `Client SPA→Blueprint` (access_as_user).

Supporting script: [setup-obo-blueprint.ps1](./scripts/setup-obo-blueprint.ps1) (bash: [setup-obo-blueprint.sh](./scripts/setup-obo-blueprint.sh))

### Step 4 — Bring Up Sidecar Stack

```bash
cd sidecar/dev
docker compose --env-file .env up --build -d
```

Verify: `curl http://localhost:3003/api/status` returns agent_app_id, ollama_available, sidecar_url.

### Step 5 — Test Flows

- **Autonomous Direct**: `curl -s -X POST http://localhost:3003/api/chat -H 'Content-Type: application/json' -d '{"message":"weather in seattle","use_langchain":false,"token_flow":"autonomous"}'` → should return Seattle weather; TR token `roles: [User.Read.All]`.
- **OBO**: open `http://localhost:3003` in a browser, sign in with MSAL popup, chat with `token_flow: obo`.

## Key Artifacts (for reuse)

Persist these in `sidecar/<target>/.env`:
- `TENANT_ID`
- `BLUEPRINT_APP_ID`
- `BLUEPRINT_CLIENT_SECRET`
- `AGENT_CLIENT_ID`
- `CLIENT_SPA_APP_ID`

## References

- [Required permissions and role comparison](./references/permissions.md)
- [Troubleshooting matrix](./references/troubleshooting.md)
- [Graph API call sequence](./references/graph-api-sequence.md)
