# Required Permissions for Entra Agent ID

## Entra Directory Role (the account running the setup scripts)

Per [Microsoft Learn — Agent 365 AI-guided setup](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference#agent-id-administrator) and the authoritative [permissions reference](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference), **one of** the following roles is required to create Agent Identity Blueprints:

| Role | Template ID | Scope | Notes |
|------|-------------|-------|-------|
| Global Administrator | `62e90394-69f5-4237-9190-012177145e10` | All | Least-specific; highest privilege |
| Agent ID Administrator | `db506228-d27e-4b7d-95e5-295956d6615f` | Agent ID-specific | **Recommended** for production setup. Full blueprint / agent / agentic-user lifecycle |
| Agent ID Developer | `adb2368d-a9be-41b5-8667-d96778e081b0` | Agent ID-specific | **Least-privileged that works — empirically verified 2026-04-21**. Per Learn: *"Create an agent blueprint and its service principal in a tenant. User will be added as an owner."* |

### Roles that DO NOT work

- `Application Administrator` (`9b895d92-2cd3-44c7-9d02-a6ac2d5ea5c3`) — can create regular apps, but **not** `agentIdentityBlueprints`. Returns `403 Authorization_RequestDenied`.
- `Cloud Application Administrator` (`158c047a-c907-4556-b7ef-446551a6b5f7`) — same limitation.

Empirical verification (anonymized test tenant):
- **2026-04-20**: App Admin + Cloud App Admin → 403 on blueprint create; Global Admin → success
- **2026-04-21**: Tester stripped to **only** `Agent ID Developer` → `Start-EntraAgentIDWorkflow` succeeded end-to-end (Blueprint + Agent created, `User.Read.All` granted, T1/T2 tokens issued). Confirms Agent ID Developer is the real floor.

## Microsoft Graph Scopes (delegated, for `Connect-MgGraph`)

Minimum set used by `Start-EntraAgentIDWorkflow` after removing AgentUser:

| Scope | Used by |
|-------|---------|
| `AgentIdentityBlueprint.Create` | POST `/beta/applications/` with `@odata.type: AgentIdentityBlueprint` |
| `AgentIdentityBlueprint.AddRemoveCreds.All` | POST `.../addPassword` |
| `AgentIdentityBlueprintPrincipal.Create` | POST `/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal` |
| `Application.Read.All` | GET blueprint object ID by appId |
| `AppRoleAssignment.ReadWrite.All` | POST `/servicePrincipals/<id>/appRoleAssignments` |
| `DelegatedPermissionGrant.ReadWrite.All` | `/oauth2PermissionGrants` |
| `Directory.Read.All` | Lookups of Graph SP + agent SP |
| `User.Read` | Initial sign-in / `/v1.0/me` |

The OBO setup script (`setup-obo-blueprint.ps1`) uses additionally:
- `AgentIdentityBlueprint.ReadWrite.All` (PATCH to set `identifierUris` + `oauth2PermissionScopes`)
- `Application.ReadWrite.All` (PATCH on Client SPA's `requiredResourceAccess`)
