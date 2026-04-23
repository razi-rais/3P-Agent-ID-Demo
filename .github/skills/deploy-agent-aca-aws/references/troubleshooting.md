# Troubleshooting matrix

| Symptom | Root cause | Fix |
|---|---|---|
| `InvalidIdentityToken: Incorrect token audience` (boto3 → STS) | MI token's audience is a GUID; STS rejects it | Use the v1-exchange pattern. See [v1-token-exchange.md](./v1-token-exchange.md). |
| `AADSTS65001: consent not granted` on OBO sign-in | Agent SP has app perms only, not delegated `User.Read` | Run `scripts/grant-agent-obo-consent.ps1`. |
| `AADSTS50011: redirect URI mismatch` in browser | SPA app has only `http://localhost:3003` | Run `scripts/add-spa-redirect-uri.sh`. |
| Graph `$filter=appId eq` returns empty for Blueprint | Agent Identity Blueprint types are invisible to `$filter` | Use key-lookup form `/beta/applications(appId='<id>')`. The PowerShell scripts in this skill already do this. |
| `Directory.AccessAsUser.All` scope required (pwsh) | `az account get-access-token --resource graph` includes this scope, which Blueprint PATCH rejects | Use `Connect-MgGraph -Scopes …` with narrow scopes (never `.default`). |
| `403 Authorization_RequestDenied` on Blueprint create | User has `Application Administrator` but not an Agent ID role | Assign `Agent ID Developer` (template `adb2368d-a9be-41b5-8667-d96778e081b0`) or `Agent ID Administrator`. |
| `AADSTS50079` on `az login` | New user hasn't completed MFA enrollment | Sign in once via browser to enroll, then retry. |
| Container App fails to pull image | MI doesn't have `AcrPull` on the registry | `az role assignment create --assignee-object-id "$MI_OBJECT_ID" --assignee-principal-type ServicePrincipal --scope "$ACR_ID" --role AcrPull`. |
| Token refresher logs `iss=…/v2.0` | Refresher fell back to v2 endpoint; exchange misconfigured | Confirm intermediary app has `requestedAccessTokenVersion=1` and `identifierUris=["api://<self>"]`. |
| CloudTrail shows no `AssumeRoleWithWebIdentity` events | Refresher writing but agent not refreshing credentials | Restart the `llm-agent` container; boto3 reads the token file on each call but caches STS creds for ~50 min. |
| `identifierUris` PATCH rejected | Tenant blocks custom `api://` URIs | Use `api://<self-appId>` form, never a custom label. |
| Sidecar image fails to start with secret-related error | `SignedAssertionFromManagedIdentity` source type not picked up | Confirm env var: `AzureAd__ClientCredentials__0__SourceType=SignedAssertionFromManagedIdentity` and `__ManagedIdentityClientId=""` (empty for system-assigned). |
| Container Apps rejects total CPU/memory | Invalid consumption combo | Totals across all containers must match a valid ACA combo. Working combo: 0.5+0.25+0.25+0.25 vCPU = 1.25, 1+0.5+0.5+0.5 = 2.5 Gi. |
| `Connect-MgGraph: InteractiveBrowserCredential authentication failed: User canceled authentication.` | `Connect-MgGraph` run inside `pwsh -Command "..."` subshell; WAM/browser popup hidden | Run `pwsh` interactively first, then `Connect-MgGraph` inside that session. Do not use `pwsh -Command` for interactive auth. |
| `Missing required Microsoft Graph scopes` from `Start-EntraAgentIDWorkflow` | `Connect-MgGraph` missing `AgentIdentityBlueprint.DeleteRestore.All` and/or `AgentIdentity.DeleteRestore.All` | Disconnect and reconnect with all 10 scopes from entra-agent-id-setup SKILL.md Step 1. |
| `Property spa in payload has a value that does not match schema.` from `az ad app update` | PowerShell JSON escaping mangles the `--set spa='{...}'` payload | Use `az rest --method PATCH` against Graph with properly serialized JSON. See post-deploy-manual-steps.md. |
| Dedicated workload profile takes 20+ minutes to provision | Normal Azure behavior | Wait. Do not cancel and retry. |
| Azure Policy blocks RG creation (`SFI-W18-*` tag policies) | Tenant requires tags on resource groups | Add required tags: `az group create --tags RGMonthlyCost=Low Owner=YourAlias` |

## Diagnostic one-liners

```bash
# Verify token refresher is writing v1 tokens
az containerapp logs show -g "$RG" -n "$APP_NAME" --container token-refresher --type console --tail 5

# Verify managed identity object ID
az containerapp show -g "$RG" -n "$APP_NAME" --query identity.principalId -o tsv

# Verify AWS role trust condition
aws iam get-role --role-name "$AWS_ROLE_NAME" --query 'Role.AssumeRolePolicyDocument' --output json

# Verify intermediary app v1 tokens
az rest --method GET --url "https://graph.microsoft.com/v1.0/applications(appId='$STS_APP_ID')?\$select=api" --query api.requestedAccessTokenVersion

# Verify Blueprint federated credential subject
az rest --method GET --url "https://graph.microsoft.com/beta/applications(appId='$BLUEPRINT_APP_ID')/federatedIdentityCredentials"
```
