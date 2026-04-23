# Troubleshooting matrix

| Symptom | Root cause | Fix |
|---|---|---|
| `ollama_available: false` in `/api/status` | Ollama container failed to start or pull model | Check logs with `az containerapp logs show --container ollama`; bump memory; switch to `baked` strategy |
| Ollama container crash-loops with `out of memory` | Model too large for allocated memory | Drop to 1.5B model, or bump ollama memory (see [ollama-on-aca.md](./ollama-on-aca.md)) |
| Ollama logs show `pulling manifest…` then 404 | Model name / tag wrong | Verify exact name with `docker run --rm ollama/ollama:latest ollama pull <name>` locally |
| First request hangs 30+ s | `runtime-pull` strategy cold start | Switch to `baked` strategy (`build-ollama-image.sh`) |
| `AADSTS65001: consent not granted` on OBO sign-in | Agent SP has app perms only, not delegated `User.Read` | Run `scripts/grant-agent-obo-consent.ps1` |
| `AADSTS50011: redirect URI mismatch` in browser | SPA app has only `http://localhost:3003` | Run `scripts/add-spa-redirect-uri.sh` |
| Graph `$filter=appId eq` returns empty for Blueprint | Agent Identity Blueprint types invisible to `$filter` | Use key-lookup form `/beta/applications(appId='<id>')` — scripts already do this |
| `Directory.AccessAsUser.All` scope required (pwsh) | `az account get-access-token --resource graph` includes this scope, which Blueprint PATCH rejects | Use `Connect-MgGraph -Scopes …` with narrow scopes (never `.default`) |
| `403 Authorization_RequestDenied` on Blueprint create | User has `Application Administrator` but not an Agent ID role | Assign `Agent ID Developer` or `Agent ID Administrator` |
| `AADSTS50079` on `az login` | New user hasn't completed MFA enrollment | Sign in once via browser to enroll, then retry |
| Container App fails to pull image (`ImagePullBackOff`) | MI doesn't have `AcrPull` on the registry | `az role assignment create --assignee-object-id "$MI_OBJECT_ID" --assignee-principal-type ServicePrincipal --scope "$ACR_ID" --role AcrPull` |
| Container Apps rejects total CPU/memory | Invalid consumption combo | Totals across all containers must match a valid ACA combo. Demo: 0.5+0.25+0.25+0.75 vCPU = 1.75; 1+0.5+0.5+1.5 = 3.5 Gi |
| Sidecar startup error about `ClientSecret` | Left over docker-compose env var | Ensure manifest uses `AzureAd__ClientCredentials__0__SourceType=SignedAssertionFromManagedIdentity` with empty `ManagedIdentityClientId` for system-assigned |
| ACA ingress returns 504 on first chat | Ollama cold-loading large model; exceeded 4-min ingress timeout | Use smaller model or Dedicated profile |
| Baked Ollama image missing from ACR after `az acr build` | ACR Build cannot run `ollama serve` in RUN instructions — the daemon silently fails, model pull hangs | **Use local `docker buildx build --push` for baked images.** ACR Build works for `llm-agent` and `weather-api` but NOT for baked Ollama. If no Docker Desktop, switch to `runtime-pull`. |
| `Connect-MgGraph: InteractiveBrowserCredential authentication failed: User canceled authentication.` | `Connect-MgGraph` run inside `pwsh -Command "..."` subshell; WAM/browser popup hidden behind windows | Run `pwsh` as an interactive session first, **then** call `Connect-MgGraph` inside it. Do not use `pwsh -Command` for interactive auth. |
| `Missing required Microsoft Graph scopes` from `Start-EntraAgentIDWorkflow` | `Connect-MgGraph` was called without `AgentIdentityBlueprint.DeleteRestore.All` and/or `AgentIdentity.DeleteRestore.All` | Disconnect and reconnect with all 10 required scopes (see entra-agent-id-setup SKILL.md Step 1). The script’s scope list is the source of truth, not the skill’s earlier examples. |
| `Property spa in payload has a value that does not match schema.` from `az ad app update` | PowerShell JSON escaping mangles the `--set spa='{...}'` payload | Use `az rest --method PATCH` against `https://graph.microsoft.com/v1.0/applications(appId='...')` with a properly serialized JSON body instead. See Step 6 in the SKILL procedure. |
| `UnicodeEncodeError: 'charmap' codec can't encode characters` from `az acr build` | Windows terminal uses cp1252 encoding; colorama output contains characters outside that range | Cosmetic — the build succeeds. Ignore the error, or set `$env:PYTHONIOENCODING = "utf-8"` before running. |
| Dedicated-D4 workload profile takes 20+ minutes to provision | Normal Azure behavior for dedicated profile provisioning | Wait. Do not cancel and retry — that restarts the timer. |
| Azure Policy blocks RG creation (`SFI-W18-Require RGMonthlyCost tag`) | Tenant has tag-enforcement policies on resource groups | Add required tags: `az group create --name "$RG" --location "$LOCATION" --tags RGMonthlyCost=Low Owner=YourAlias` |

## Diagnostic one-liners

```bash
# Verify MI object ID
az containerapp show -g "$RG" -n "$APP_NAME" --query identity.principalId -o tsv

# Verify Blueprint federated credential subject
az rest --method GET --url "https://graph.microsoft.com/beta/applications(appId='$BLUEPRINT_APP_ID')/federatedIdentityCredentials"

# Verify Ollama served model list
curl -sS "https://${APP_FQDN}/api/status" | python3 -m json.tool

# Tail Ollama logs
az containerapp logs show -g "$RG" -n "$APP_NAME" --container ollama --tail 50

# Tail sidecar logs (for Entra auth errors)
az containerapp logs show -g "$RG" -n "$APP_NAME" --container sidecar --tail 50
```
