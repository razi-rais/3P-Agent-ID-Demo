# Local Dev Sidecar (Ollama Edition)

A visual demonstration of how AI agents use **Microsoft Entra Agent ID** to securely call downstream APIs. This edition runs entirely on your laptop with a local LLM via [Ollama](https://ollama.com) — no cloud LLM provider required.

> **New to Agent ID?** Start with the [Sidecar Guide](../SIDECAR-GUIDE.md) for the fundamentals. This sample builds on that with a complete end-to-end demo.

---

## What this sample shows

- **Two execution modes**: Direct tool call (fast, no LLM) vs LangChain + Ollama (agentic tool calling)
- **Two identity flows**: Autonomous agent (app-only) vs On-Behalf-Of (OBO, acting for a signed-in user)
- **Full token lifecycle**: Tc (user token) → T1 (blueprint app token) → TR (agent token) → downstream API
- **JWT validation**: The weather API verifies signature (JWKS / RS256), issuer, and expiry on every request
- **LangGraph ReAct agent**: Modern LangChain 1.x pattern with `langchain.agents.create_agent`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         agent-network-dev                               │
│                                                                         │
│  ┌──────────────────┐        ┌───────────────────────────────────────┐  │
│  │  llm-agent-dev   │───────▶│  agent-id-sidecar-dev                 │  │
│  │  (Flask + UI)    │        │  (Microsoft Entra SDK)                │  │
│  │  :3000→host:3003 │◀───────│  No host port (trust boundary)        │  │
│  └────────┬─────────┘        └──────────────────┬────────────────────┘  │
│           │                                     │                       │
│           │ Bearer TR                           │ OAuth2 to Entra ID    │
│           ▼                                     ▼                       │
│  ┌──────────────────┐                  ┌────────────────────┐           │
│  │  weather-api-dev │                  │ login.microsoft... │           │
│  │  (validates JWT) │                  │  (Entra ID)        │           │
│  └──────────────────┘                  └────────────────────┘           │
│                                                                         │
│  ┌──────────────────┐                                                   │
│  │    ollama-dev    │  ← used only when Execution Mode = Ollama         │
│  │  (qwen2.5:1.5b)  │                                                   │
│  └──────────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Token flow

| Token | Issued to | When | How |
|---|---|---|---|
| **Tc** | Signed-in user | OBO flow only | MSAL.js in the browser |
| **T1** | Blueprint app | Both flows | Sidecar (client credentials) |
| **TR** | Agent (downstream API) | Both flows | Sidecar — app-only (autonomous) or OBO exchange |

---

## Modes and flows (2×2 matrix)

|                     | **Autonomous** (app-only) | **OBO** (on behalf of user) |
|---------------------|----------------------------|------------------------------|
| **Direct** (no LLM) | Fast demo path. TR token fetched, weather API called directly. | Same, but uses the authenticated sidecar endpoint with Tc. |
| **Ollama + LangChain** | LangGraph ReAct agent decides when to call the `get_weather` tool. | Same, agent passes Tc through when the tool runs. |

---

## Prerequisites

1. **Docker Desktop** running
2. **A registered Agent ID in Microsoft Entra** — run the PowerShell workflow in the repo root (see [SIDECAR-GUIDE.md](../SIDECAR-GUIDE.md)) to create:
   - Blueprint app (with client secret)
   - Agent ID
   - SPA app (for OBO browser sign-in)
3. Optional: [Ollama](https://ollama.com) model. The docker-compose pulls `qwen2.5:1.5b` automatically.

---

## Quick start

```bash
cd sidecar/dev

# Copy the template and fill in values from your PowerShell workflow
cp .env.example .env
$EDITOR .env

# Build and start everything
docker compose up --build -d

# Open the demo
open http://localhost:3003
```

Wait ~30 seconds on first run while Ollama pulls the model. Check readiness:

```bash
curl http://localhost:3003/api/status
# {"ollama_available": true, "ollama_model": "qwen2.5:1.5b", ...}
```

---

## Environment variables

See [.env.example](./.env.example) for the full template.

| Variable | Description |
|---|---|
| `TENANT_ID` | Your Entra tenant ID |
| `BLUEPRINT_APP_ID` | Blueprint app registration — the sidecar authenticates as this app |
| `BLUEPRINT_CLIENT_SECRET` | Blueprint client secret (dev only — see below) |
| `AGENT_CLIENT_ID` | Your Agent ID (appears as `AgentIdentity` query param) |
| `CLIENT_SPA_APP_ID` | SPA app ID used by MSAL.js for browser sign-in (OBO only) |
| `OLLAMA_MODEL` | Default `qwen2.5:1.5b`. Larger models give better tool calling. |

### Blueprint credential — pick the right `SourceType`

The sidecar supports multiple credential types via `AzureAd__ClientCredentials__0__SourceType` in [docker-compose.yml](./docker-compose.yml):

| SourceType | When to use |
|---|---|
| `ClientSecret` | **Local dev only** — what this sample ships with |
| `SignedAssertionFromManagedIdentity` | **Production on Azure** — zero secrets, recommended |
| `KeyVault` | Certificate from Azure Key Vault |
| `StoreWithThumbprint` | Certificate from local machine store |

Reference: [microsoft-identity-web / Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)

---

## Services

| Service | Container | Host port | Role |
|---|---|---|---|
| `llm-agent` | `llm-agent-dev` | **3003** | Flask app + chat UI + LangChain agent |
| `sidecar` | `agent-id-sidecar-dev` | *none* | Microsoft Entra SDK — issues tokens |
| `weather-api` | `weather-api-dev` | *none* | Downstream API, validates JWT on every request |
| `ollama` | `ollama-dev` | *none* | Local LLM — only used in Ollama mode |

> **Security note:** Only the UI is exposed to the host. The sidecar, weather-api and Ollama are reachable only within the Docker network, per [Microsoft's trust-boundary guidance](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/security).

---

## Using the UI

The page has two toggles:

- **Execution Mode** — `Direct` (skip LLM) or `Ollama` (LangChain ReAct agent)
- **Identity Flow** — `Autonomous` (app-only token) or `OBO` (requires user sign-in)

When you pick **OBO**, a **Sign in** button appears. MSAL.js acquires the Tc user token. On submit, the agent sends Tc to the sidecar, which exchanges it for the OBO TR token and calls the weather API as that user.

The right-hand panel shows the full token trace — sidecar URLs, JWT claims (color-coded by token type), what the weather API validates, and the final result.

---

## Running the tests

Unit tests cover JWT decode, debug logging, all Flask routes, input validation, city extraction, and LangChain agent creation.

```bash
cd sidecar/dev
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v
```

Expected: **28 passed in ~4s, zero warnings**.

---

## LangChain version and architecture

| Package | Pinned | Role |
|---|---|---|
| `langchain` | `>=1.0.0` | Hosts `create_agent` (LangGraph ReAct builder) |
| `langchain-core` | `>=1.0.0` | `@tool` decorator, message types |
| `langchain-ollama` | `>=1.0.0` | `ChatOllama` provider |
| `langgraph` | `>=1.0.0` | Underlying agent runtime |

The agent is a **LangGraph ReAct agent** built with [`langchain.agents.create_agent`](https://docs.langchain.com/oss/python/langchain/agents) — this is the current pattern as of LangChain 1.x. The older `AgentExecutor` / `langgraph.prebuilt.create_react_agent` paths are deprecated and no longer used.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/status` → `ollama_available: false` | Model still downloading | Wait ~30s, check `docker logs ollama-dev` |
| Weather API returns `401 Unauthorized` | Token tenant mismatch, expired secret, or signature check failed | Verify `TENANT_ID` matches the blueprint's tenant; check sidecar logs |
| LLM returns weather without calling the tool | `qwen2.5:1.5b` is too small for reliable tool calling | Switch `OLLAMA_MODEL` to `qwen2.5:7b` or `llama3.1:8b` |
| OBO sign-in popup blocked | Browser popup blocker | Allow popups for `localhost:3003` |
| `4xx` from sidecar during OBO | `CLIENT_SPA_APP_ID` missing or SPA redirect URI mismatch | Re-run the PowerShell workflow; ensure `http://localhost:3003` is on the SPA's redirect URIs |

Container logs:

```bash
docker logs llm-agent-dev
docker logs agent-id-sidecar-dev
docker logs weather-api-dev
```

---

## Stop & cleanup

```bash
# Stop containers, keep volumes/images
docker compose down

# Also remove the Ollama model cache
docker compose down -v

# Nuke everything (containers, volumes, images)
docker compose down -v --rmi all
```

---

## Files

```
sidecar/dev/
├── app.py               # Flask app + LangGraph ReAct agent + sidecar client
├── docker-compose.yml   # llm-agent, sidecar, weather-api, ollama
├── Dockerfile           # Python 3.11 slim base
├── requirements.txt     # LangChain 1.x, Flask, MSAL
├── .env.example         # Template — copy to .env
├── templates/
│   └── index.html       # Chat UI, MSAL.js, token trace panel
├── tests/
│   ├── __init__.py
│   └── test_app.py      # 28 pytest tests
└── docs/
    └── images/          # Screenshots
```

---

## References

- [Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/identity-platform/agent-identity/)
- [Auth Sidecar container](https://mcr.microsoft.com/en-us/product/entra-sdk/auth-sidecar/about)
- [LangChain Agents (v1)](https://docs.langchain.com/oss/python/langchain/agents)
- [microsoft-identity-web Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)
