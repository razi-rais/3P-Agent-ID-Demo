# Troubleshooting

## 403 `Authorization_RequestDenied` on `POST /beta/applications/`

**Symptom**: `Start-EntraAgentIDWorkflow` fails at blueprint creation even though the tester can create regular apps.

**Cause**: The signed-in user only has `Application Administrator` and/or `Cloud Application Administrator`. These roles lack the `microsoft.directory/agentIdentityBlueprints/create` permission.

**Fix**: Assign one of: `Global Administrator`, `Agent ID Administrator`, or `Agent ID Developer`. See [permissions.md](./permissions.md).

## 403 on `POST /beta/users/microsoft.graph.agentUser`

**Symptom**: Creating an `AgentUser` fails even as Global Administrator. Filter `userType eq 'AgentUser'` returns BadRequest.

**Cause**: `AgentUser` is a preview / Frontier-gated feature not enabled on every tenant.

**Fix**: Not required for autonomous or OBO flows. Our scripts do not create AgentUsers. If you specifically need them, request enablement for your tenant via Microsoft.

## `AADSTS50079` — User must enroll in MFA

**Symptom**: `az login -u <newuser> -p <pw>` fails for a freshly created user.

**Cause**: Conditional Access requires MFA registration before non-interactive flows.

**Fix**: Sign in once via a browser (`az login --tenant <id>` → opens browser) to complete MFA enrollment, then subsequent `az login` works.

## `AADSTS7000215` — Invalid client secret in T1→T2 exchange

**Symptom**: Blueprint secret rejected shortly after creation.

**Cause**: Azure AD propagation delay (~30 s).

**Fix**: `New-AgentIdentityBlueprint` already retries up to 10×3s. If still failing, wait 60s and retry the workflow.

## "Service Principal not found" when adding permissions

**Symptom**: `Add-AgentIdentityPermissions` errors with "Resource does not exist".

**Cause**: Agent SP not yet queryable (propagation).

**Fix**: Script auto-retries 5×3s. If it exhausts, check that the Agent Identity was created — rerun `Get-AgentIdentityList`.

## OBO sign-in popup blocked / redirect URI mismatch

**Symptom**: MSAL sign-in popup blocked or `AADSTS50011: redirect_uri mismatch`.

**Cause**: Client SPA's redirect URI ≠ `http://localhost:3003` (the agent's host port).

**Fix**:
```bash
az ad app update --id <CLIENT_SPA_APP_ID> \
  --set 'spa={"redirectUris":["http://localhost:3003"]}'
```

## Autonomous TR token returned but Graph call 401

**Symptom**: TR token acquired from sidecar but Weather API / Graph call 401s.

**Causes to check**:
1. `roles` claim in TR is empty → permission not granted. Re-run `Add-AgentIdentityPermissions`.
2. `aud` in TR ≠ `https://graph.microsoft.com` → scope mismatch.
3. TR signed by wrong tenant `iss` → `AGENT_CLIENT_ID` or `TENANT_ID` in `.env` stale.

Use `Get-DecodedJwtToken -Token $tr` to inspect.

## Container startup: `platform linux/amd64 does not match arm64`

**Symptom**: Warning on Apple Silicon when pulling the sidecar image.

**Cause**: Microsoft publishes `linux/amd64` only. Docker runs it under Rosetta.

**Fix**: Harmless — the sidecar runs correctly under emulation.

## `Connect-MgGraph: InteractiveBrowserCredential authentication failed: User canceled authentication.`

**Symptom**: `Connect-MgGraph` immediately fails when run from `pwsh -Command "Connect-MgGraph ..."` subshell.

**Cause**: WAM popup or browser window opens behind other windows in a child `pwsh` process. The popup isn’t visible and times out.

**Fix**: Run `pwsh` as an interactive session first, then call `Connect-MgGraph` inside it. Do NOT wrap it in `pwsh -Command "..."` on Windows.

## `Missing required Microsoft Graph scopes` from `Start-EntraAgentIDWorkflow`

**Symptom**: Workflow throws even though `Connect-MgGraph` succeeded.

**Cause**: `Connect-MgGraph` was called without `AgentIdentityBlueprint.DeleteRestore.All` and/or `AgentIdentity.DeleteRestore.All`. The SKILL previously listed only 8 scopes; the script validates 10.

**Fix**: `Disconnect-MgGraph` then reconnect with the full 10-scope list from the SKILL.md Step 1 code block. The script’s internal scope check is the canonical source of truth.
