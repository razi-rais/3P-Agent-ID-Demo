# Deploy to Azure App Service — with zero stored secrets

This guide walks through a production deployment of the AWS Bedrock sidecar sample to **Azure App Service**, using **OIDC federation** so that **no AWS or Entra credentials are ever stored anywhere**.

End state:

- Containers running on App Service for Containers
- The sidecar authenticates to **Microsoft Entra** as the Blueprint app via **`SignedAssertionFromManagedIdentity`** — no client secret
- The agent calls **AWS Bedrock** via **`AssumeRoleWithWebIdentity`** using a token issued by Azure to the App Service's **system-assigned managed identity** — no AWS access keys
- All credentials are short-lived and auto-rotated by their respective platforms

> **Why this matters:** the dev workflow in [`README.md`](./README.md) puts secrets in a local `.env`. That's fine on a laptop. In production, no human (and no leaked log line, build artifact, or ex-employee) should ever be able to walk away with a long-lived credential.

---

## Architecture (production)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Azure App Service (Linux container, multi-container or single)     │
│                                                                     │
│  ┌────────────────────┐    ┌────────────────────────┐               │
│  │  llm-agent-aws     │    │  agent-id-sidecar-aws  │               │
│  │  (Flask + boto3)   │◀──▶│  (Microsoft Entra SDK) │               │
│  └─────────┬──────────┘    └───────────┬────────────┘               │
│            │                           │                            │
│            │ SignedAssertion           │ MI token (federated)        │
│            │ from MI (for AWS)         │ from MI (for Entra)         │
│            ▼                           ▼                            │
│      ╔═══════════════════════════════════════╗                      │
│      ║  System-Assigned Managed Identity     ║                      │
│      ║  (issued by Entra, rotates ~24h)      ║                      │
│      ╚═══════════════════════════════════════╝                      │
└────────────────┬─────────────────────────┬──────────────────────────┘
                 │                         │
                 │ AssumeRole              │ client_credentials
                 │ WithWebIdentity         │ + OBO
                 ▼                         ▼
         ┌──────────────┐          ┌─────────────────┐
         │  AWS STS     │          │  Entra ID       │
         │  ↓           │          │  login.micro... │
         │  Temp creds  │          └─────────────────┘
         │  (1h TTL)    │
         └──────┬───────┘
                ▼
         ┌──────────────┐
         │ AWS Bedrock  │
         │ InvokeModel  │
         └──────────────┘
```

**Two federation chains, one identity:** the App Service's managed identity is the only thing that exists. AWS trusts it via OIDC; Entra trusts it via federated identity credentials on the Blueprint app.

---

## 0. Prerequisites

| Need | Notes |
|---|---|
| Azure subscription | With permission to create App Service Plan, App Service, Managed Identity, ACR, and assign roles |
| AWS account with Bedrock model access enabled | Anthropic Claude 3 Haiku enabled in your region |
| `az` CLI logged in | `az login` |
| `aws` CLI v2 logged in | An IAM principal with permission to create OIDC providers and IAM roles |
| Docker | To build and push images to ACR |
| Repo cloned locally | This file lives at `sidecar/aws/DEPLOY-AZURE-APP-SERVICE.md` |
| Entra Agent ID already created | You have `TENANT_ID`, `BLUEPRINT_APP_ID`, `AGENT_CLIENT_ID` (and `CLIENT_SPA_APP_ID` for OBO) — see the dev README §6.2 |

> Replace every `<PLACEHOLDER>` below with your real value. Variables used throughout:
>
> | Variable | Example |
> |---|---|
> | `<TENANT_ID>` | `e325cd37-67d5-421b-9ca3-40108b21d74a` |
> | `<SUBSCRIPTION_ID>` | `4d58f8cd-1502-4a5f-acd0-1bc4aec89325` |
> | `<RG>` | `rg-agent-id-prod` |
> | `<LOCATION>` | `eastus2` |
> | `<ACR_NAME>` | `agentidprod` (must be globally unique) |
> | `<APP_NAME>` | `agent-id-aws-prod` (must be globally unique) |
> | `<AWS_ACCOUNT_ID>` | `123456789012` |
> | `<AWS_REGION>` | `us-east-2` |
> | `<BEDROCK_MODEL_ID>` | `us.anthropic.claude-3-haiku-20240307-v1:0` |
> | `<BLUEPRINT_APP_ID>` | the Blueprint application (client) ID |
> | `<AGENT_CLIENT_ID>` | the Agent ID |
> | `<CLIENT_SPA_APP_ID>` | the SPA app for OBO (omit if autonomous-only) |

---

## Step 1 — Configure AWS to trust Entra as an OIDC identity provider

This is a **one-time setup per Entra tenant**. AWS needs to know that tokens issued by `https://login.microsoftonline.com/<TENANT_ID>/v2.0` are trustworthy.

```bash
aws iam create-open-id-connect-provider \
  --url "https://login.microsoftonline.com/<TENANT_ID>/v2.0" \
  --client-id-list "api://AzureADTokenExchange" \
  --thumbprint-list "626d44e704d1ceabe3bf0d53397464ac8080142c"
```

Notes:

- The `client-id-list` value (`api://AzureADTokenExchange`) is the **audience** AWS will require in tokens it accepts. This is a fixed Microsoft convention.
- The thumbprint is documented at [AWS — Obtaining the thumbprint for an OpenID Connect identity provider](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc_verify-thumbprint.html). Verify it once with the AWS docs / OpenSSL; AWS now recomputes it server-side and the field is largely cosmetic, but a value is still required.
- Save the **Provider ARN** that's printed:
  ```
  arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/login.microsoftonline.com/<TENANT_ID>/v2.0
  ```

> Already have the OIDC provider from a previous deployment? Verify with:
> ```bash
> aws iam list-open-id-connect-providers
> ```

---

## Step 2 — Create the IAM role that App Service will assume

The role's **trust policy** says *"only Azure managed identities I name explicitly can assume me"*. The **permissions policy** says *"once assumed, you may invoke Bedrock and nothing else"*.

### 2.1 Create the App Service first (so we have a managed identity to point the trust policy at)

We do this out of order on purpose: AWS needs the managed identity's **object ID** before we can scope the trust policy. So we'll create the App Service skeleton now and come back later in Step 5 to configure it.

```bash
# Resource group
az group create --name <RG> --location <LOCATION>

# Container registry
az acr create --resource-group <RG> --name <ACR_NAME> --sku Basic --admin-enabled false

# App Service Plan (Linux)
az appservice plan create \
  --name <APP_NAME>-plan \
  --resource-group <RG> \
  --location <LOCATION> \
  --is-linux \
  --sku B2

# App Service (placeholder image — we'll swap to our own in Step 4)
az webapp create \
  --resource-group <RG> \
  --plan <APP_NAME>-plan \
  --name <APP_NAME> \
  --deployment-container-image-name "mcr.microsoft.com/appsvc/staticsite:latest"

# Enable system-assigned managed identity
az webapp identity assign --resource-group <RG> --name <APP_NAME>

# Capture the MI principal/object ID — you'll need it in 2.2
MI_OBJECT_ID=$(az webapp identity show --resource-group <RG> --name <APP_NAME> --query principalId -o tsv)
echo "MI object id: $MI_OBJECT_ID"
```

### 2.2 Write the trust policy (replace the two placeholders)

Save as `trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/login.microsoftonline.com/<TENANT_ID>/v2.0"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "login.microsoftonline.com/<TENANT_ID>/v2.0:aud": "api://AzureADTokenExchange",
          "login.microsoftonline.com/<TENANT_ID>/v2.0:sub": "<MI_OBJECT_ID>"
        }
      }
    }
  ]
}
```

> **Why this is tight:** the `sub` condition pins the role to **this exact managed identity only**. No other identity in your tenant — not even another App Service in the same subscription — can assume it. Rotating the App Service's MI (e.g. by deleting + recreating the App Service) requires re-pointing this policy.

### 2.3 Write the permissions policy

Save as `bedrock-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:<AWS_REGION>::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
        "arn:aws:bedrock:*:<AWS_ACCOUNT_ID>:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0"
      ]
    }
  ]
}
```

> **Scope tightly.** This grants `InvokeModel` on **only** the specific Haiku model and its US inference profile. If you switch models later, add the new ARN. Never use `Resource: "*"` here.

### 2.4 Create the role and attach the policy

```bash
aws iam create-role \
  --role-name BedrockInvokerFromAzure \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name BedrockInvokerFromAzure \
  --policy-name BedrockInvokeOnly \
  --policy-document file://bedrock-policy.json

# Save the role ARN — you'll need it in Step 5
aws iam get-role --role-name BedrockInvokerFromAzure --query 'Role.Arn' --output text
# → arn:aws:iam::<AWS_ACCOUNT_ID>:role/BedrockInvokerFromAzure
```

---

## Step 3 — Switch the sidecar to `SignedAssertionFromManagedIdentity`

The dev sidecar config in [`docker-compose.yml`](./docker-compose.yml) uses `SourceType=ClientSecret` with the secret pulled from `.env`. For production, swap it to managed identity — no secret stored anywhere.

### 3.1 Add a federated identity credential to the Blueprint app

This tells **Entra** to accept tokens issued by the App Service's managed identity as proof of identity *for the Blueprint app*.

```bash
# Get the App Service's managed-identity object ID (same value from Step 2.1)
MI_OBJECT_ID=$(az webapp identity show --resource-group <RG> --name <APP_NAME> --query principalId -o tsv)

# The 'issuer' for an Azure MI assertion
MI_ISSUER="https://login.microsoftonline.com/<TENANT_ID>/v2.0"
MI_AUDIENCE="api://AzureADTokenExchange"

# Add the federated credential to the Blueprint app
az ad app federated-credential create \
  --id <BLUEPRINT_APP_ID> \
  --parameters "{
    \"name\": \"app-service-managed-identity\",
    \"issuer\": \"$MI_ISSUER\",
    \"subject\": \"$MI_OBJECT_ID\",
    \"audiences\": [\"$MI_AUDIENCE\"],
    \"description\": \"App Service MI assumes the Blueprint app\"
  }"
```

### 3.2 Update the sidecar config

In your production `docker-compose.yml` (or App Service multi-container manifest), **replace** the `ClientSecret` block on the sidecar service:

```yaml
# REMOVE these (dev-only):
# - AzureAd__ClientCredentials__0__SourceType=ClientSecret
# - AzureAd__ClientCredentials__0__ClientSecret=${BLUEPRINT_CLIENT_SECRET}

# ADD these (production):
- AzureAd__ClientCredentials__0__SourceType=SignedAssertionFromManagedIdentity
- AzureAd__ClientCredentials__0__ManagedIdentityClientId=    # leave blank for system-assigned MI
```

> Reference: [microsoft-identity-web — Client Credentials → Federated identity from a managed identity](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)

You can now **delete `BLUEPRINT_CLIENT_SECRET` from your records** entirely. You may even revoke the secret in the Azure Portal (Blueprint app → Certificates & secrets → Delete) once the deployment is verified.

---

## Step 4 — Build and push container images to ACR

From the repo root:

```bash
# Log in to ACR
az acr login --name <ACR_NAME>

# Build + push the agent image (multi-arch optional — App Service runs linux/amd64)
docker buildx build \
  --platform linux/amd64 \
  -t <ACR_NAME>.azurecr.io/agent-id-aws/llm-agent:1.0.0 \
  -t <ACR_NAME>.azurecr.io/agent-id-aws/llm-agent:latest \
  --push \
  sidecar/aws

# Build + push the weather-api image (you only need this if you keep the demo
# weather API in production — for a real deployment, point at a real downstream API)
docker buildx build \
  --platform linux/amd64 \
  -t <ACR_NAME>.azurecr.io/agent-id-aws/weather-api:1.0.0 \
  --push \
  sidecar/weather-api
```

The Microsoft Entra SDK sidecar image (`mcr.microsoft.com/entra-sdk/auth-sidecar:1.0.0-azurelinux3.0-distroless`) is pulled from MCR directly — no need to push it to your ACR.

### 4.1 Grant App Service permission to pull from ACR

```bash
# Use the App Service MI to pull (no admin user, no static creds)
ACR_ID=$(az acr show --name <ACR_NAME> --query id -o tsv)
az role assignment create \
  --assignee "$MI_OBJECT_ID" \
  --scope "$ACR_ID" \
  --role "AcrPull"
```

---

## Step 5 — Configure the App Service (no secrets!)

### 5.1 Wire the App Service to your image

```bash
az webapp config container set \
  --resource-group <RG> \
  --name <APP_NAME> \
  --container-image-name "<ACR_NAME>.azurecr.io/agent-id-aws/llm-agent:1.0.0" \
  --container-registry-url "https://<ACR_NAME>.azurecr.io"

# Tell App Service to use the MI when pulling
az webapp config set \
  --resource-group <RG> \
  --name <APP_NAME> \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}'
```

> For the multi-container case (agent + sidecar + weather-api in one App Service), use **Sidecar Containers for Azure App Service** — see the [official docs](https://learn.microsoft.com/en-us/azure/app-service/tutorial-custom-container-sidecar). The principle below is identical: each container reads env vars from App Settings.

### 5.2 Set App Settings — the entire production config, with **no secrets**

```bash
az webapp config appsettings set \
  --resource-group <RG> \
  --name <APP_NAME> \
  --settings \
    WEBSITES_PORT=3000 \
    TENANT_ID="<TENANT_ID>" \
    BLUEPRINT_APP_ID="<BLUEPRINT_APP_ID>" \
    AGENT_CLIENT_ID="<AGENT_CLIENT_ID>" \
    CLIENT_SPA_APP_ID="<CLIENT_SPA_APP_ID>" \
    SIDECAR_URL="http://localhost:5000" \
    WEATHER_API_URL="http://localhost:8080" \
    AWS_REGION="<AWS_REGION>" \
    BEDROCK_MODEL_ID="<BEDROCK_MODEL_ID>" \
    AWS_ROLE_ARN="arn:aws:iam::<AWS_ACCOUNT_ID>:role/BedrockInvokerFromAzure" \
    AWS_WEB_IDENTITY_TOKEN_FILE="/tmp/azure-token"
```

> **Notice what is NOT set:**
>
> - ❌ no `BLUEPRINT_CLIENT_SECRET`
> - ❌ no `AWS_ACCESS_KEY_ID`
> - ❌ no `AWS_SECRET_ACCESS_KEY`
> - ❌ no `AWS_SESSION_TOKEN`
> - ❌ no `AWS_BEARER_TOKEN_BEDROCK`
>
> If you ever see any of these in App Settings, the deployment has regressed.

### 5.3 Provide the Azure token to boto3

`AWS_WEB_IDENTITY_TOKEN_FILE` tells boto3 to read an **Entra-issued JWT** from that path and use it as the OIDC assertion in `AssumeRoleWithWebIdentity`. App Service does **not** populate this file for you — you need a tiny helper that:

1. Calls the **App Service IMDS** (`http://169.254.169.254/metadata/identity/oauth2/token?api-version=2019-08-01&resource=api://AzureADTokenExchange`) using the `IDENTITY_ENDPOINT` / `IDENTITY_HEADER` env vars Azure injects
2. Writes the returned `access_token` to `/tmp/azure-token`
3. Refreshes it before expiry (every ~50 minutes)

The simplest pattern is a **separate sidecar container** running this loop. Drop the following into `sidecar/aws/azure-token-refresher/refresh.py` and ship it as the third container:

```python
# refresh.py — Refresh Azure MI token to file every 50 minutes
import os, time, json, urllib.request

IMDS_URL = (
    f"{os.environ['IDENTITY_ENDPOINT']}"
    "?api-version=2019-08-01"
    "&resource=api%3A%2F%2FAzureADTokenExchange"
)
HEADERS = {"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]}
OUT = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE", "/tmp/azure-token")

while True:
    req = urllib.request.Request(IMDS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        token = json.loads(r.read())["access_token"]
    with open(OUT, "w") as f:
        f.write(token)
    os.chmod(OUT, 0o600)
    time.sleep(50 * 60)
```

Mount `/tmp` as a shared volume across the agent and refresher containers. boto3 then transparently calls `AssumeRoleWithWebIdentity` whenever it needs Bedrock credentials, refreshing the resulting STS creds every hour automatically.

> **Why not bake this into `app.py`?** Separation of concerns. The refresher has only one job; you can replace it with [`aws-azure-login`](https://github.com/sportradar/aws-azure-login) or any other helper without touching the agent.

---

## Step 6 — Deploy and verify

### 6.1 Restart and warm up

```bash
az webapp restart --resource-group <RG> --name <APP_NAME>
```

Tail the logs while the container starts:

```bash
az webapp log tail --resource-group <RG> --name <APP_NAME>
```

You should see:

- The **token refresher** writing `/tmp/azure-token` on first iteration
- The **sidecar** booting and reporting `SourceType=SignedAssertionFromManagedIdentity`
- The **agent** reporting `bedrock_available: true` from `/api/status`

### 6.2 Functional check

```bash
APP_HOST="<APP_NAME>.azurewebsites.net"
curl https://$APP_HOST/api/status
# {"bedrock_available": true, "bedrock_model": "us.anthropic.claude-3-haiku-20240307-v1:0", ...}
```

Open `https://<APP_NAME>.azurewebsites.net` in your browser, set **Execution Mode = Bedrock** + **Identity Flow = Autonomous**, send *"Weather in Dallas?"*, and confirm the Identity Trace panel shows the same `0.A → 5.` flow you see locally.

### 6.3 Verify in CloudTrail that no static creds were used

In the AWS console → **CloudTrail → Event history**, filter on:

- **Event source:** `sts.amazonaws.com`
- **Event name:** `AssumeRoleWithWebIdentity`

You should see entries every ~1 hour, with:

- `userIdentity.type` = `WebIdentityUser`
- `userIdentity.identityProvider` = `https://login.microsoftonline.com/<TENANT_ID>/v2.0`
- `requestParameters.roleArn` = `arn:aws:iam::<AWS_ACCOUNT_ID>:role/BedrockInvokerFromAzure`

If you ever see `AssumeRole` (instead of `AssumeRoleWithWebIdentity`) from this app, or `AccessDenied` events on `InvokeModel`, the federation chain has broken — start from Step 1.

---

## Step 7 — Rotation, revocation, and incident response

| Scenario | Action |
|---|---|
| Suspect the App Service is compromised | `az webapp identity remove --name <APP_NAME> --resource-group <RG>` — instantly breaks federation; AWS will reject `AssumeRoleWithWebIdentity` from this MI |
| Suspect the AWS role is overly broad | Edit `bedrock-policy.json`, re-apply with `aws iam put-role-policy` — takes effect immediately |
| Tenant migration | Re-run Step 1 (new OIDC provider URL with new `<TENANT_ID>`), update trust policy in Step 2.2, update federated credential in Step 3.1 |
| MI rotation (rare — only on App Service recreate) | Capture new `MI_OBJECT_ID`, update both the AWS role trust policy and the Entra federated credential |
| AWS credential rotation | **None required** — STS credentials are auto-rotated by boto3 every ~1 hour |
| Entra credential rotation | **None required** — managed identity tokens auto-rotate (~24h) |

---

## Step 8 — Cost notes

| Component | Approx. monthly cost (low traffic) |
|---|---|
| App Service Plan B2 (Linux) | ~$55 |
| ACR Basic | ~$5 |
| Bedrock Claude 3 Haiku | $0.00025 / 1K input tokens — negligible at demo volumes |
| AWS STS / OIDC federation | Free |
| Azure managed identity | Free |
| CloudTrail (management events) | First copy free |

For lower production cost, swap App Service Plan to **B1** (~$13/mo) or use **Azure Container Apps** (consumption billing) — the federation setup is identical.

---

## Appendix A — Why OIDC federation, not access keys?

| Concern | Static AWS access keys | OIDC federation (this guide) |
|---|---|---|
| Stored in App Settings | Yes — in plaintext, visible to anyone with reader role | No |
| Survive a leak | Until manually revoked (often months) | ~1 hour max (STS TTL) |
| Per-environment scoping | Manual key-per-env hygiene | Trust policy per role per environment |
| Audit trail | "User Bob's key was used" — same key everywhere | Each session is its own CloudTrail event tied to the MI's `sub` |
| Rotation | Manual, error-prone, breaks deployments | Automatic, invisible |

The setup is a few extra steps once. After that, you can grep your entire infra for AWS access keys and find none.

---

## Appendix B — Common errors

| Error | Where | Cause | Fix |
|---|---|---|---|
| `InvalidIdentityToken: Couldn't retrieve verification key` | boto3 → STS | OIDC provider not registered or wrong issuer URL | Re-run Step 1; check the URL exactly matches `https://login.microsoftonline.com/<TENANT_ID>/v2.0` (note: trailing slash matters in some SDK versions) |
| `AccessDenied: Not authorized to perform sts:AssumeRoleWithWebIdentity` | boto3 → STS | Trust policy `sub` or `aud` mismatch | Verify the MI object ID in Step 2.2 matches the current MI; verify audience is `api://AzureADTokenExchange` |
| `AccessDeniedException: User is not authorized to perform: bedrock:InvokeModel` | App | IAM permissions policy missing the model ARN | Add the model ARN to `bedrock-policy.json`, re-apply |
| `MsalUiRequiredException` from sidecar | Sidecar logs | Federated credential not configured on Blueprint app | Re-run Step 3.1 |
| `The audience 'api://AzureADTokenExchange' is invalid` | Sidecar logs | App Settings missing or MI not enabled | Verify `az webapp identity show` returns a `principalId`; verify Step 3.1 used that exact value |

---

## References

- [`AssumeRoleWithWebIdentity` API reference](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html)
- [AWS — OIDC identity providers](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
- [Azure — Workload identity federation overview](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation)
- [Azure — Federated identity credentials on app registrations](https://learn.microsoft.com/en-us/graph/api/resources/federatedidentitycredential)
- [microsoft-identity-web — Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)
- [Sidecar containers in Azure App Service](https://learn.microsoft.com/en-us/azure/app-service/tutorial-custom-container-sidecar)
- [App Service — Use managed identities for ACR pull](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container#use-managed-identity-to-pull-image-from-azure-container-registry)
