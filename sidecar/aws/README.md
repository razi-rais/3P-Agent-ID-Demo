# AWS Bedrock Sidecar (Cloud LLM Edition)

A visual, hands-on demonstration of how AI agents use **Microsoft Entra Agent ID** — via the official **Microsoft Entra SDK auth sidecar** — to securely call downstream APIs. This variant uses **AWS Bedrock** (Anthropic Claude) as the LLM, proving the sidecar pattern works identically across clouds.

> **Looking for the local-only version?** See [`sidecar/dev`](../dev/README.md) — same architecture, runs entirely offline with Ollama.
>
> **New to Agent ID?** Start with the [Sidecar Guide](../SIDECAR-GUIDE.md) for the fundamentals.

---

## 1. Why the Microsoft Entra SDK sidecar?

This sample deliberately uses the **official [Microsoft Entra SDK auth sidecar](https://mcr.microsoft.com/en-us/product/entra-sdk/auth-sidecar/about)** container (`mcr.microsoft.com/entra-sdk/auth-sidecar`) rather than rolling our own token client. Here's why:

- **Interoperable across any cloud or on-prem** — the same container image runs identically on Azure, AWS, GCP, Kubernetes, or a laptop. This sample puts it next to **AWS Bedrock** to make the cross-cloud story concrete: the LLM is in AWS, identity is in Microsoft Entra, and neither side cares.
- **Your agent code stays decoupled from token exchanges.** The agent never handles `client_id`, `client_secret`, certificates, JWKS, token caching, or OBO exchange. It just asks the sidecar: *"Give me an authorization header for this downstream API."*
- **Swap credentials without touching agent code.** `ClientSecret` for dev, `SignedAssertionFromManagedIdentity` for production on Azure — change one env var, no code changes.
- **Token caching, refresh, and expiry are handled for you.** No MSAL integration to debug.
- **Security boundary is explicit.** The sidecar has no host port. Only services inside the Docker network can request tokens — your agent, not your browser, not random processes on the host.

### What the agent does vs what the sidecar does

| Agent (your code) | Sidecar (Microsoft Entra SDK) |
|---|---|
| Decide *when* to call the API | Acquire and cache the right token |
| Build the HTTP request | Perform client-credentials / OBO exchange |
| Pass through user token for OBO | Validate & forward user assertion |
| Handle business logic | Talk to `login.microsoftonline.com` |

---

## 2. What this sample demonstrates

- **Two execution modes**: `Direct` (skip LLM, fast demo of token flow) vs `Bedrock` (LangChain + Claude makes the tool-call decision)
- **Two identity flows**: `Autonomous` (app-only token) vs `OBO` (acts on behalf of a signed-in user)
- **Full token lifecycle**: Tc (user token) → T1 (blueprint app token) → TR (agent token) → downstream API
- **JWT validation end-to-end**: The weather API verifies signature (JWKS / RS256), issuer, and expiry on every request
- **LangGraph ReAct agent**: Modern LangChain 1.x pattern with `langchain.agents.create_agent`
- **Three production-ready AWS auth tiers** documented: temporary STS creds, Bedrock API keys, and OIDC federation (no secrets) — see [§5](#5-aws-authentication--pick-the-right-tier)

### Modes and flows (2×2 matrix)

|                     | **Autonomous** (app-only) | **OBO** (on behalf of user) |
|---------------------|----------------------------|------------------------------|
| **Direct** (no LLM) | Fast demo path. TR token fetched, weather API called directly. | Same, but uses the authenticated sidecar endpoint with Tc. |
| **Bedrock + LangChain** | LangGraph ReAct agent decides when to call `get_weather`. | Same, agent passes Tc through when the tool runs. |

---

## 3. Architecture

The sidecar sits between your agent and Microsoft Entra ID. The agent **never** talks to Entra directly, and it **never** sees a credential — it just asks the sidecar for an `Authorization:` header for a named downstream API. Bedrock is just the LLM provider; identity is owned by the sidecar.

### 3.1 High-level flow (the 30-second view)

```
   ┌──────────┐  ask     ┌──────────┐  get token   ┌──────────┐
   │  Agent   │────────▶ │ Sidecar  │ ───────────▶ │  Entra   │
   │ (Flask + │          │ (Entra   │ ◀─────────── │   ID     │
   │ Bedrock) │◀──────── │   SDK)   │   TR token   └──────────┘
   └────┬─────┘ header   └──────────┘
        │
        │ call API with Bearer TR
        ▼
   ┌──────────┐
   │ Weather  │   validates TR, returns data
   │   API    │
   └──────────┘

   ┌──────────────┐   LLM inference (separate concern)
   │ AWS Bedrock  │ ◀── agent calls this for reasoning
   └──────────────┘
```

**Three identity moving parts, one rule:** the **Agent** focuses on reasoning, the **Sidecar** owns all identity/credential work, the **downstream API** just validates the token it's given. The LLM (Bedrock) is orthogonal — it never touches identity.

### 3.2 Detailed architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                     agent-network-aws (Docker bridge)                         │
│                                                                               │
│  You (browser)                                                                │
│   http://localhost:3001 ────┐                                                 │
│                             ▼                                                 │
│   ┌──────────────────────────────────┐    AWS SDK   ┌─────────────────┐       │
│   │  llm-agent-aws  (Flask + UI)     │ ───────────▶ │  AWS Bedrock    │       │
│   │  :3000 → host :3001              │ ◀─────────── │  Claude (Haiku) │       │
│   │                                  │   inference  └─────────────────┘       │
│   │  ① Receive user query            │                                        │
│   │  ② LangGraph ReAct agent runs    │                                        │
│   │  ③ Tool needs to call weather API│                                        │
│   │     → ask sidecar for a token    │                                        │
│   └──────────────┬───────────────────┘                                        │
│                  │ ④ GET /AuthorizationHeader...                              │
│                  │    ?AgentIdentity={agentId}                                │
│                  │    (Bearer Tc if OBO)                                      │
│                  ▼                                                            │
│   ┌──────────────────────────────────┐    ⑤ OAuth2  ┌─────────────────┐      │
│   │  agent-id-sidecar-aws            │ ───────────▶ │  Microsoft      │      │
│   │  Microsoft Entra SDK             │              │  Entra ID       │      │
│   │  (official MS container image)   │ ◀─────────── │  login.micro... │      │
│   │  NO host port — network only     │   ⑥ T1 / TR  └─────────────────┘      │
│   └──────────────┬───────────────────┘                                        │
│                  │ ⑦ Authorization: Bearer TR                                 │
│                  ▼                                                            │
│   ┌──────────────────────────────────┐                                        │
│   │  weather-api-aws                 │                                        │
│   │  ⑧ Validate TR (JWKS, RS256,     │                                        │
│   │    issuer, expiry, audience)     │                                        │
│   │  ⑨ Return weather JSON           │                                        │
│   └──────────────────────────────────┘                                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

**The key insight:** steps ⑤ and ⑥ are the *only* place an Entra credential is ever handled, and that happens inside the sidecar on a network the agent can't reach from outside. AWS credentials are similarly isolated — your agent code at step ③ does `requests.get(sidecar_url)` (for Entra) and `boto3.client("bedrock-runtime")` (for AWS); no MSAL, no certificates, no AWS keys hard-coded.

### Token flow (Microsoft Entra side)

| Token | Issued to | When | How |
|---|---|---|---|
| **Tc** | Signed-in user | OBO flow only | MSAL.js in the browser |
| **T1** | Blueprint app | Both flows | Sidecar (client credentials) |
| **TR** | Agent (downstream API) | Both flows | Sidecar — app-only (autonomous) or OBO exchange |

### What the Identity Trace panel shows

```
✓ 0.A START               User query received
✓ 0.B BEDROCK             Sending to AWS Bedrock
✓ 0.C AGENT READY         LangChain agent created
✓ 1.B TOOL CALL           LLM decides to call get_weather
✓ 2.A TOKEN REQUEST       Request Agent Identity token
✓ 2.B SIDECAR CALL        Sidecar URL with AgentIdentity=…
✓ 2.C TOKEN RECEIVED      TR JWT received (decoded claims shown)
✓ 3.A API CALL            Calling Weather API
✓ 3.B API URL             Weather endpoint + Authorization header
✓ 3.C API RESPONSE        Weather data received (full JSON)
✓ 4.  TOOL RESULT         Tool execution complete
✓ 5.  COMPLETE            Response sent to user
```

For OBO, you'll additionally see **Tc** (user token from MSAL) and **T1** (blueprint app-only token) cards before the **TR**.

---

## 4. Prerequisites

Works on **macOS**, **Linux**, and **Windows 10/11**.

| Need | macOS | Linux | Windows |
|---|---|---|---|
| Docker | Docker Desktop | Docker Engine + Compose v2 | Docker Desktop (WSL 2 backend recommended) |
| PowerShell 7+ | `brew install --cask powershell` | [install docs](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-linux) | built-in (or install PS 7+) |
| Azure CLI | `brew install azure-cli` | [install docs](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux) | `winget install -e Microsoft.AzureCLI` |
| AWS CLI v2 | `brew install awscli` | [install docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) | `winget install -e Amazon.AWSCLI` |
| Python 3.11+ (only if you run tests) | `brew install python@3.11` | distro package | `winget install -e Python.Python.3.11` |

You also need:

1. **A registered Agent ID in Microsoft Entra** — the repo-root PowerShell workflow creates the Blueprint app, client secret, and Agent ID. See [§6.2](#62-first-time-setup--create-the-entra-objects).
2. **An AWS account with Bedrock model access** — by default this sample uses `us.anthropic.claude-3-haiku-20240307-v1:0`. Enable model access in the AWS Bedrock console (*Model access → Manage model access → Anthropic Claude 3 Haiku → Save*). Approval is usually instant.
3. **AWS credentials** for one of the three tiers in [§5](#5-aws-authentication--pick-the-right-tier).

---

## 5. AWS authentication — pick the right tier

The sample supports three ways to authenticate to Bedrock. Pick based on where you're running it.

| Tier | Use when | What you set | Lifetime | Secrets at rest? |
|---|---|---|---|---|
| **A. Temporary STS creds** | Local dev, your laptop, your AWS SSO | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_SESSION_TOKEN` in `.env` | ~1 hour | Yes (in gitignored `.env`) |
| **B. Bedrock API key** | Public demos, workshops, hands-on labs | `AWS_BEARER_TOKEN_BEDROCK` in `.env` | Long- or short-term, scoped to Bedrock only | Yes (in gitignored `.env`) |
| **C. OIDC federation** | **Production on Azure App Service** | Platform App Settings (no `.env`); `AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE` | Auto-rotated by AWS STS | **No** — zero stored secrets |

> **Production deployment instructions:** see **[`DEPLOY-AZURE-APP-SERVICE.md`](./DEPLOY-AZURE-APP-SERVICE.md)** for the full step-by-step guide using tier (C) — Azure Managed Identity → AWS IAM role via OIDC federation, with no AWS keys ever stored anywhere.

`.env.example` documents all three tiers with side-by-side examples.

---

## 6. Run it and open the UI

> **Supported hosts:** macOS, Linux, and Windows 10/11. Every command below is given for **bash** (macOS / Linux / WSL / Git Bash) and **PowerShell 7+** (Windows). Pick the one that matches your shell.

### 6.1 Do you already have an `.env` from a previous run?

If **yes** — you've already run the tenant setup once and have `sidecar/aws/.env` populated with `TENANT_ID`, `BLUEPRINT_APP_ID`, `BLUEPRINT_CLIENT_SECRET`, `AGENT_CLIENT_ID`, and (for OBO) `CLIENT_SPA_APP_ID` — **skip to [6.3 Start the stack](#63-start-the-stack)**.

> **Why?** All the Entra objects (Blueprint app, client secret, Agent ID, SPA app registration, OBO scope consent) are tenant-side state. They survive `docker compose down`, reboots, and git resets. You only need to set them up once per tenant.

Not sure? Run the matching snippet:

**bash**

```bash
cd sidecar/aws
test -f .env && grep -q '^BLUEPRINT_APP_ID=.\+' .env && echo "✓ .env looks ready" || echo "✗ run 6.2 first"
```

**PowerShell**

```powershell
Cd sidecar/aws
if ((Test-Path .env) -and (Select-String '^BLUEPRINT_APP_ID=.+' .env -Quiet)) { "✓ .env looks ready" } else { "✗ run 6.2 first" }
```

### 6.2 First-time setup — create the Entra objects

Run this **once per tenant**. It creates the Blueprint app, Agent ID, and the SPA app used for OBO sign-in.

**a. Create Blueprint + Agent ID** (autonomous flow only)

Follow the PowerShell workflow in the **[repo root README](../../README.md)** (works on macOS, Linux and Windows with [PowerShell 7+](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell)). At the end you'll have:

- `TENANT_ID` — your Entra tenant
- `BLUEPRINT_APP_ID` — Blueprint app registration
- `BLUEPRINT_CLIENT_SECRET` — client secret for the Blueprint
- `AGENT_CLIENT_ID` — the Agent ID created from the Blueprint

**b. Create the SPA app + wire up OBO** (required for OBO flow only)

**bash**

```bash
bash ../../scripts/setup-obo-client-app.sh
bash ../../scripts/setup-obo-blueprint.sh
```

**PowerShell**

```powershell
pwsh ../../scripts/setup-obo-client-app.ps1
pwsh ../../scripts/setup-obo-blueprint.ps1 `
    -TenantId        '<TENANT_ID>' `
    -BlueprintAppId  '<BLUEPRINT_APP_ID>' `
    -AgentAppId      '<AGENT_CLIENT_ID>' `
    -ClientSpaAppId  '<CLIENT_SPA_APP_ID>'
```

> **Note:** the SPA redirect URI for this sample is `http://localhost:3001` (port 3001, not 3003). Make sure that's what's registered.

**c. Populate `.env`**

**bash**

```bash
cp .env.example .env
"${EDITOR:-vi}" .env
```

**PowerShell**

```powershell
Copy-Item .env.example .env
notepad .env   # or: code .env
```

Minimum required for **autonomous flow**: `TENANT_ID`, `BLUEPRINT_APP_ID`, `BLUEPRINT_CLIENT_SECRET`, `AGENT_CLIENT_ID`, plus an AWS auth tier from [§5](#5-aws-authentication--pick-the-right-tier).
Additionally required for **OBO flow**: `CLIENT_SPA_APP_ID`.

See [§7](#7-environment-variables) for details on each variable.

### 6.3 Start the stack

`docker compose` is identical on all hosts — make sure **Docker Desktop** (macOS / Windows) or the **Docker Engine** (Linux) is running first.

```bash
cd sidecar/aws
docker compose up --build -d
```

Check readiness:

**bash**

```bash
curl http://localhost:3001/api/status
# {"bedrock_available": true, "bedrock_model": "us.anthropic.claude-3-haiku-20240307-v1:0", ...}
```

**PowerShell**

```powershell
Invoke-RestMethod http://localhost:3001/api/status
# bedrock_available : True
```

> **⚠️ When you change `.env`** (e.g. STS creds expired and you pasted new ones), `docker compose restart` will **not** reload them. Use:
> ```bash
> docker compose up -d --force-recreate llm-agent-aws
> ```

### 6.4 Open the UI

**→ [http://localhost:3001](http://localhost:3001)** ← the only port exposed to your host.

A two-panel layout:

- **Left panel — Chat**
  - Header bar shows your **Tenant ID** and **Agent ID**
  - Two toggles control the demo:
    - **Execution Mode**: `Direct` (skip LLM) or `Bedrock` (LangChain ReAct agent on Claude)
    - **Identity Flow**: `Autonomous` (app-only token) or `OBO` (acts for signed-in user)
  - Input is pre-populated with *"Weather in Dallas?"* — press Send
  - When **Identity Flow = OBO**, a **Sign in** button appears (MSAL.js popup)

- **Right panel — Identity Trace**
  - Step-by-step debug trace of every token exchange and API call
  - Color-coded JWT cards for each token (**Tc** / **T1** / **TR**) with decoded claims
  - Shows exactly what the weather API validates on each request

**Ports exposed:**

| Port | Service | Access |
|---|---|---|
| **3001** | Chat UI | `http://localhost:3001` — you |
| *none* | Sidecar, weather API | Docker network only (trust boundary) |

---

## 7. Environment variables

See [.env.example](./.env.example) for the full template with the three AWS auth tiers.

| Variable | Description |
|---|---|
| `TENANT_ID` | Your Entra tenant ID |
| `BLUEPRINT_APP_ID` | Blueprint app registration — the sidecar authenticates as this app |
| `BLUEPRINT_CLIENT_SECRET` | Blueprint client secret (dev only — see below) |
| `AGENT_CLIENT_ID` | Your Agent ID (appears as `AgentIdentity` query param) |
| `CLIENT_SPA_APP_ID` | SPA app ID used by MSAL.js for browser sign-in (OBO only) |
| `AWS_REGION` | AWS region for Bedrock — e.g. `us-east-2` |
| `BEDROCK_MODEL_ID` | Default `us.anthropic.claude-3-haiku-20240307-v1:0` (cheapest); inference profile IDs prefixed `us.` route across regions |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` | **Tier A** only — temporary STS creds from `aws sso login` |
| `AWS_BEARER_TOKEN_BEDROCK` | **Tier B** only — Bedrock API key |
| `AWS_ROLE_ARN` / `AWS_WEB_IDENTITY_TOKEN_FILE` | **Tier C** only — set by App Service, not in `.env` |
| `VALIDATE_TOKEN_SIGNATURE` | Default `true`. Set `false` to disable JWKS signature validation in the weather API (dev debugging only) |

### Blueprint credential — pick the right `SourceType`

The sidecar supports multiple credential types via `AzureAd__ClientCredentials__0__SourceType` in [docker-compose.yml](./docker-compose.yml):

| SourceType | When to use |
|---|---|
| `ClientSecret` | **Local dev only** — what this sample ships with |
| `SignedAssertionFromManagedIdentity` | **Production on Azure** — zero secrets, recommended (see [`DEPLOY-AZURE-APP-SERVICE.md`](./DEPLOY-AZURE-APP-SERVICE.md)) |
| `KeyVault` | Certificate from Azure Key Vault |
| `StoreWithThumbprint` | Certificate from local machine store |

Reference: [microsoft-identity-web / Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)

---

## 8. Services

| Service | Container | Host port | Role |
|---|---|---|---|
| `llm-agent-aws` | `llm-agent-aws` | **3001** | Flask app + chat UI + LangChain agent + Bedrock client |
| `sidecar` | `agent-id-sidecar-aws` | *none* | Microsoft Entra SDK — issues tokens |
| `weather-api` | `weather-api-aws` | *none* | Downstream API, validates JWT on every request |

> **Security note:** Only the UI is exposed to the host. The sidecar and weather-api are reachable only within the Docker network, per [Microsoft's trust-boundary guidance](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/security).

---

## 9. Running the tests

Unit tests cover JWT decode, debug logging, all Flask routes, input validation, city extraction, and LangChain agent creation.

**bash**

```bash
cd sidecar/aws
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v
```

**PowerShell**

```powershell
Cd sidecar/aws
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

Expected: **27 passed, 1 skipped** in under a second.

---

## 10. LangChain version and architecture

| Package | Pinned | Role |
|---|---|---|
| `langchain` | `>=1.0.0` | Hosts `create_agent` (LangGraph ReAct builder) |
| `langchain-core` | `>=1.0.0` | `@tool` decorator, message types |
| `langchain-aws` | `>=0.2.0` | `ChatBedrockConverse` provider |
| `langgraph` | `>=1.0.0` | Underlying agent runtime |

The agent is a **LangGraph ReAct agent** built with [`langchain.agents.create_agent`](https://docs.langchain.com/oss/python/langchain/agents) — this is the current pattern as of LangChain 1.x. The older `AgentExecutor` / `langgraph.prebuilt.create_react_agent` paths are deprecated and no longer used.

---

## 11. Bedrock model selection

This sample defaults to **`us.anthropic.claude-3-haiku-20240307-v1:0`** because it's the cheapest Anthropic model on Bedrock and supports tool calling. The `us.` prefix indicates a **cross-region inference profile** that routes between US regions for higher availability.

| Model ID | Cost per 1K input tokens | Notes |
|---|---|---|
| `us.anthropic.claude-3-haiku-20240307-v1:0` | $0.00025 | Default. Fast, cheapest, supports tool calling. |
| `us.anthropic.claude-3-5-haiku-20241022-v1:0` | $0.0008 | Newer, smarter, still cheap. |
| `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | $0.003 | Best quality / cost ratio. |

Override via `BEDROCK_MODEL_ID` in `.env`. You must enable each model in the **AWS Bedrock console → Model access** before it can be invoked.

---

## 12. Production deployment

The dev workflow above puts secrets in `.env`. **Do not deploy that to production.** For Azure App Service, follow the dedicated guide:

**→ [`DEPLOY-AZURE-APP-SERVICE.md`](./DEPLOY-AZURE-APP-SERVICE.md)**

It walks through, step by step:

1. Configuring AWS to trust Azure as an OIDC identity provider
2. Creating an IAM role scoped to `bedrock:InvokeModel` only
3. Building and pushing container images to Azure Container Registry
4. Deploying to App Service with system-assigned managed identity
5. Switching the Entra sidecar from `ClientSecret` to `SignedAssertionFromManagedIdentity`
6. Verifying with CloudTrail that no static AWS credentials exist anywhere

End state: **zero secrets** stored in App Settings, repo, or container images.

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/status` → `bedrock_available: false` | AWS creds missing/expired or model access not granted | Check `docker logs llm-agent-aws`; refresh STS via `aws sso login`; enable model in Bedrock console |
| `ExpiredTokenException` from Bedrock | STS session token (Tier A) expired (~1h) | Paste fresh creds in `.env`, then `docker compose up -d --force-recreate llm-agent-aws` (NOT `restart` — `restart` won't reload env) |
| `AccessDeniedException` on `InvokeModel` | IAM principal lacks `bedrock:InvokeModel` on the model ARN, or model access not enabled | Grant `bedrock:InvokeModel` on `arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-*` and the inference profile ARN |
| `ValidationException: invalid model identifier` | Region doesn't host that model, or you used the bare model ID instead of the `us.` inference profile | Use the `us.`-prefixed inference profile (e.g. `us.anthropic.claude-3-haiku-20240307-v1:0`) |
| Weather API returns `401 Unauthorized` | Token tenant mismatch, expired secret, or signature check failed | Verify `TENANT_ID` matches the blueprint's tenant; check sidecar logs |
| LLM responds without calling the tool | Prompt wasn't clearly tool-shaped, or model doesn't support tool calling | Use Claude 3 Haiku or newer; phrase the request as *"What's the weather in <city>?"* |
| OBO sign-in popup blocked | Browser popup blocker | Allow popups for `localhost:3001` |
| `4xx` from sidecar during OBO | `CLIENT_SPA_APP_ID` missing or SPA redirect URI mismatch | Re-run `setup-obo-client-app`; ensure `http://localhost:3001` is on the SPA's redirect URIs |

Container logs:

```bash
docker logs llm-agent-aws
docker logs agent-id-sidecar-aws
docker logs weather-api-aws
```

---

## 14. Stop & cleanup

```bash
# Stop containers, keep volumes/images
docker compose down

# Nuke everything (containers, volumes, images)
docker compose down -v --rmi all
```

---

## 15. Files

```
sidecar/aws/
├── app.py                            # Flask app + LangGraph ReAct agent + Bedrock client
├── docker-compose.yml                # llm-agent-aws, sidecar, weather-api
├── Dockerfile                        # Python 3.11 slim base
├── requirements.txt                  # LangChain 1.x, langchain-aws, Flask, MSAL
├── .env.example                      # Template — copy to .env (3 AWS auth tiers documented)
├── DEPLOY-AZURE-APP-SERVICE.md       # Production deployment with OIDC federation
├── templates/
│   └── index.html                    # Chat UI, MSAL.js, identity trace panel
└── tests/
    ├── __init__.py
    └── test_app.py                   # 28 pytest tests (27 pass, 1 skip)
```

---

## 16. References

- [Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/identity-platform/agent-identity/)
- [Microsoft Entra SDK auth sidecar (container image)](https://mcr.microsoft.com/en-us/product/entra-sdk/auth-sidecar/about)
- [LangChain Agents (v1)](https://docs.langchain.com/oss/python/langchain/agents)
- [microsoft-identity-web Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)
- [AWS Bedrock — supported foundation models](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html)
- [Cross-region inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- [`AssumeRoleWithWebIdentity` — OIDC federation](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html)
