# Local Dev Sidecar (Ollama Edition)

A visual, hands-on demonstration of how AI agents use **Microsoft Entra Agent ID** — via the official **Microsoft Entra SDK auth sidecar** — to securely call downstream APIs. Runs entirely on your laptop with a local LLM via [Ollama](https://ollama.com).

> **TODO:** Screenshots of the UI (chat view, token trace panel, OBO sign-in) need to be captured and added to `docs/images/` before publishing.

> **New to Agent ID?** Start with the [Sidecar Guide](../SIDECAR-GUIDE.md) for the fundamentals. This sample builds on that with a complete end-to-end demo.

---

## 1. Why the Microsoft Entra SDK sidecar?

This sample deliberately uses the **official [Microsoft Entra SDK auth sidecar](https://mcr.microsoft.com/en-us/product/entra-sdk/auth-sidecar/about)** container (`mcr.microsoft.com/entra-sdk/auth-sidecar`) rather than rolling our own token client. Here's why:

- **Certified implementation of the OAuth2 flows you actually need** — client credentials, OBO, and federated credentials — written and maintained by the identity team.
- **Your agent code stays decoupled from token exchanges.** The LLM agent never handles `client_id`, `client_secret`, certificates, JWKS, token caching, or OBO exchange. It just asks the sidecar: *"Give me an authorization header for this downstream API."*
- **Swap credentials without touching agent code.** `ClientSecret` for dev, `SignedAssertionFromManagedIdentity` for production on Azure — change one env var, no code changes.
- **Token caching, refresh, and expiry are handled for you.** No MSAL integration to debug.
- **Security boundary is explicit.** The sidecar has no host port. Only services inside the Docker network can request tokens — your agent, not your browser, not random processes on the host.
- **Portable pattern.** The same sidecar image runs under Docker Compose, Azure Container Apps (as a sidecar container), Kubernetes (as a sidecar pod), or App Service multi-container. Same config, same behavior.

### What the agent does vs what the sidecar does

| Agent (your code) | Sidecar (Microsoft Entra SDK) |
|---|---|
| Decide *when* to call the API | Acquire and cache the right token |
| Build the HTTP request | Perform client-credentials / OBO exchange |
| Pass through user token for OBO | Validate & forward user assertion |
| Handle business logic | Talk to `login.microsoftonline.com` |

If you're shipping an agent to production, **this separation is the recommended pattern** — your code never sees a secret, and all credential policy lives in one place.

---

## 2. What this sample demonstrates

- **Two execution modes**: Direct tool call (fast, no LLM) vs LangChain + Ollama (agentic tool calling)
- **Two identity flows**: Autonomous agent (app-only) vs On-Behalf-Of (OBO, acting for a signed-in user)
- **Full token lifecycle**: Tc (user token) → T1 (blueprint app token) → TR (agent token) → downstream API
- **JWT validation end-to-end**: The weather API verifies signature (JWKS / RS256), issuer, and expiry on every request
- **LangGraph ReAct agent**: Modern LangChain 1.x pattern with `langchain.agents.create_agent`

---

## 3. Architecture

The sidecar sits between your agent and Microsoft Entra ID. The agent **never** talks to Entra directly, and it **never** sees a credential — it just asks the sidecar for an `Authorization:` header for a named downstream API.

### 3.1 High-level flow (the 30-second view)

```
     ┌──────────┐   ask     ┌──────────┐  get token   ┌──────────┐
     │  Agent   │ ────────▶ │ Sidecar  │ ───────────▶ │  Entra   │
     │ (Flask + │           │ (Entra   │ ◀─────────── │   ID     │
     │  LLM)    │ ◀──────── │   SDK)   │   TR token   └──────────┘
     └────┬─────┘  header   └──────────┘
          │
          │ call API with Bearer TR
          ▼
     ┌──────────┐
     │ Weather  │   validates TR, returns data
     │   API    │
     └──────────┘
```

**Three moving parts, one rule:** the **Agent** focuses on reasoning, the **Sidecar** owns all identity/credential work, the **downstream API** just validates the token it's given. Swap the LLM, swap the API, swap the credential type — the sidecar contract (`GET /AuthorizationHeader…`) stays the same.

### 3.2 Detailed architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                     agent-network-dev (Docker bridge)                         │
│                                                                               │
│                                      ─────────── request path ────────────▶   │
│                                      ◀───────── response path ─────────────   │
│                                                                               │
│  You (browser)                                                                │
│   http://localhost:3003 ────┐                                                 │
│                             │                                                 │
│                             ▼                                                 │
│   ┌──────────────────────────────────┐                                        │
│   │  llm-agent-dev  (Flask + UI)     │                                        │
│   │  :3000 → host :3003              │                                        │
│   │                                  │                                        │
│   │  ① Receive user query            │                                        │
│   │  ② LangGraph ReAct agent runs    │                                        │
│   │  ③ Tool needs to call weather API│                                        │
│   │     → ask sidecar for a token    │                                        │
│   └──────────────┬───────────────────┘                                        │
│                  │ ④ GET /AuthorizationHeader...                              │
│                  │    ?AgentIdentity={agentId}                                │
│                  │    (Bearer Tc if OBO)                                      │
│                  ▼                                                            │
│   ┌──────────────────────────────────┐      ⑤ OAuth2   ┌─────────────────┐   │
│   │  agent-id-sidecar-dev            │ ──────────────▶ │  Microsoft      │   │
│   │  Microsoft Entra SDK             │                 │  Entra ID       │   │
│   │  (official MS container image)   │ ◀────────────── │  login.micro... │   │
│   │  NO host port — network only     │   ⑥ T1 or TR    │                 │   │
│   │                                  │                 └─────────────────┘   │
│   │  Responsibilities:               │                                        │
│   │   • client_credentials flow      │                                        │
│   │   • OBO exchange (Tc+T1 → TR)    │                                        │
│   │   • Token caching & refresh      │                                        │
│   │   • Credential management        │                                        │
│   │     (ClientSecret, ManagedId,    │                                        │
│   │      KeyVault, certificate…)     │                                        │
│   └──────────────┬───────────────────┘                                        │
│                  │ ⑦ Authorization: Bearer TR                                 │
│                  ▼                                                            │
│   ┌──────────────────────────────────┐                                        │
│   │  weather-api-dev                 │                                        │
│   │                                  │                                        │
│   │  ⑧ Validate TR (JWKS, RS256,     │                                        │
│   │    issuer, expiry, audience)     │                                        │
│   │  ⑨ Return weather JSON           │                                        │
│   └──────────────────────────────────┘                                        │
│                                                                               │
│   ┌──────────────────────────────────┐                                        │
│   │   ollama-dev (qwen2.5:1.5b)      │  ← only when Execution Mode = Ollama   │
│   └──────────────────────────────────┘                                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

**The key insight:** step ⑤ and ⑥ are the *only* place a credential is ever handled. It happens inside the sidecar, on a network the agent can't directly reach from outside. Your agent code at step ③ just does `requests.get(sidecar_url)` — no MSAL, no certificates, no secrets in application memory.

### Token flow

| Token | Issued to | When | How |
|---|---|---|---|
| **Tc** | Signed-in user | OBO flow only | MSAL.js in the browser |
| **T1** | Blueprint app | Both flows | Sidecar (client credentials) |
| **TR** | Agent (downstream API) | Both flows | Sidecar — app-only (autonomous) or OBO exchange |

### Modes and flows (2×2 matrix)

|                     | **Autonomous** (app-only) | **OBO** (on behalf of user) |
|---------------------|----------------------------|------------------------------|
| **Direct** (no LLM) | Fast demo path. TR token fetched, weather API called directly. | Same, but uses the authenticated sidecar endpoint with Tc. |
| **Ollama + LangChain** | LangGraph ReAct agent decides when to call the `get_weather` tool. | Same, agent passes Tc through when the tool runs. |

---

## 4. Sequence diagrams

### 4.1 Autonomous flow (app-only)

No user, no sign-in. The agent is authenticated as itself.

```mermaid
sequenceDiagram
    actor User as User Browser
    participant Flask as Flask App<br/>(llm-agent-dev, :3003)
    participant LangChain as LangGraph<br/>ReAct agent
    participant Tool as get_weather<br/>tool
    participant Ollama as Ollama<br/>qwen2.5:1.5b
    participant Sidecar as Entra SDK Sidecar<br/>(agent-id-sidecar-dev)
    participant Entra as Microsoft<br/>Entra ID
    participant WeatherAPI as Weather API<br/>(weather-api-dev)

    User->>Flask: 1. "What's the weather in Dallas?"
    Flask->>LangChain: 2. Invoke agent (autonomous)
    LangChain->>Ollama: 3. Route query
    Ollama->>LangChain: 4. Tool call: get_weather("Dallas")
    LangChain->>Tool: 5. Execute tool

    Note over Tool,Entra: Token acquisition — handled entirely by the sidecar
    Tool->>Sidecar: 6. GET /AuthorizationHeaderUnauthenticated/graph-app<br/>?AgentIdentity={agentAppId}
    Sidecar->>Entra: 7. client_credentials<br/>(client_id=BlueprintAppId, secret/FIC)
    Entra->>Sidecar: 8. TR (app-only, idtyp=app)
    Note right of Sidecar: Token cached for reuse
    Sidecar->>Tool: 9. Authorization: Bearer TR

    Tool->>WeatherAPI: 10. GET /weather?city=Dallas<br/>Authorization: Bearer TR
    WeatherAPI->>WeatherAPI: 11. Validate TR<br/>(JWKS, issuer, expiry, audience)
    WeatherAPI->>Tool: 12. Weather JSON

    Tool->>LangChain: 13. Tool result
    LangChain->>Ollama: 14. Format final response
    Ollama->>LangChain: 15. "Dallas is 72°F, sunny"
    LangChain->>Flask: 16. Response + debug trace
    Flask->>User: 17. Chat reply + token trace panel
```

### 4.2 OBO flow (on-behalf-of a signed-in user)

The agent acts for a specific user. The sidecar performs a 3-step exchange and the downstream API sees a *delegated* token.

```mermaid
sequenceDiagram
    actor User as User Browser
    participant MSAL as MSAL.js<br/>(in browser)
    participant EntraLogin as Entra ID<br/>(login endpoint)
    participant Flask as Flask App<br/>(llm-agent-dev, :3003)
    participant LangChain as LangGraph<br/>ReAct agent
    participant Tool as get_weather<br/>tool
    participant Ollama as Ollama<br/>qwen2.5:1.5b
    participant Sidecar as Entra SDK Sidecar<br/>(agent-id-sidecar-dev)
    participant Entra as Entra ID<br/>(token endpoint)
    participant WeatherAPI as Weather API<br/>(weather-api-dev)

    Note over User,EntraLogin: Phase 1 — User sign-in (MSAL.js)
    User->>MSAL: 1. Click "Sign in"
    MSAL->>EntraLogin: 2. Interactive login (popup)
    EntraLogin->>MSAL: 3. Tc (user access token)
    Note right of MSAL: Tc audience = api://{BlueprintAppId}

    Note over User,WeatherAPI: Phase 2 — Agent query with OBO
    User->>Flask: 4. "Weather in Dallas?" + Bearer Tc
    Flask->>LangChain: 5. Invoke agent (OBO)
    LangChain->>Ollama: 6. Route query
    Ollama->>LangChain: 7. Tool call: get_weather("Dallas")
    LangChain->>Tool: 8. Execute tool (with Tc)

    Note over Tool,Entra: Phase 3 — OBO token exchange (inside sidecar)
    Tool->>Sidecar: 9. GET /AuthorizationHeader/graph<br/>Authorization: Bearer Tc<br/>?AgentIdentity={agentAppId}
    Sidecar->>Sidecar: 10. Validate Tc
    Sidecar->>Entra: 11. client_credentials<br/>→ T1 (Blueprint, idtyp=app)
    Entra->>Sidecar: 12. T1
    Sidecar->>Entra: 13. OBO exchange<br/>assertion=Tc, client_assertion=T1<br/>grant_type=jwt-bearer<br/>requested_token_use=on_behalf_of
    Entra->>Sidecar: 14. TR (delegated, idtyp=user)
    Note right of Sidecar: TR acts on behalf of signed-in user
    Sidecar->>Tool: 15. Authorization: Bearer TR

    Note over Tool,WeatherAPI: Phase 4 — Downstream call with delegated token
    Tool->>WeatherAPI: 16. GET /weather?city=Dallas<br/>Authorization: Bearer TR
    WeatherAPI->>WeatherAPI: 17. Validate TR (delegated)
    WeatherAPI->>Tool: 18. Weather JSON

    Tool->>LangChain: 19. Tool result
    LangChain->>Ollama: 20. Format response
    Ollama->>LangChain: 21. "Dallas is 72°F"
    LangChain->>Flask: 22. Response + debug trace
    Flask->>User: 23. Chat reply (Tc/T1/TR cards visible)
```

### 4.3 What the Identity Trace panel shows

```
✅ 0.A START                User query received
✅ 0.B LANGCHAIN           Sending to LangGraph ReAct agent
✅ 1.B TOOL CALL           LLM decides to call get_weather
✅ 2.A TOKEN REQUEST       Request Agent Identity token
✅ 2.B SIDECAR CALL        Sidecar URL with AgentIdentity=…
✅ 2.C TOKEN RECEIVED      TR JWT received (decoded claims shown)
✅ 3.A API CALL            Calling Weather API
✅ 3.B API URL             Weather endpoint + Authorization header
✅ 3.C TOKEN VALIDATION    What the API checks (JWKS, iss, exp, aud)
✅ 3.D API RESPONSE        Weather data received (full JSON)
✅ 4.  TOOL RESULT         Tool execution complete
✅ 5.  COMPLETE            Response sent to user
```

For OBO, you'll additionally see **Tc** (user token from MSAL) and **T1** (blueprint app-only token) cards before the **TR**.

---

## 5. Prerequisites

1. **Docker Desktop** running
2. **A registered Agent ID in Microsoft Entra** — run the PowerShell workflow in the repo root (see [SIDECAR-GUIDE.md](../SIDECAR-GUIDE.md)) to create:
   - Blueprint app (with client secret)
   - Agent ID
   - SPA app (for OBO browser sign-in)
3. Optional: [Ollama](https://ollama.com) model. The docker-compose pulls `qwen2.5:1.5b` automatically.

---

## 6. Environment variables

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

## 7. Run it and open the UI

```bash
cd sidecar/dev

# Copy the template and fill in values from your PowerShell workflow
cp .env.example .env
$EDITOR .env

# Build and start everything
docker compose up --build -d
```

Open the chat UI in your browser:

**→ [http://localhost:3003](http://localhost:3003)** ← this is the only port exposed to your host.

Wait ~30 seconds on first run while Ollama pulls the model. Check readiness:

```bash
curl http://localhost:3003/api/status
# {"ollama_available": true, "ollama_model": "qwen2.5:1.5b", ...}
```

### What you'll see

A two-panel layout:

- **Left panel — Chat**
  - Header bar shows your **Tenant ID** and **Agent ID**
  - Two toggles control the demo:
    - **Execution Mode**: `Direct` (skip LLM) or `Ollama` (LangChain ReAct agent)
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
| **3003** | Chat UI | `http://localhost:3003` — you |
| *none* | Sidecar, weather API, Ollama | Docker network only (trust boundary) |

---

## 8. Services

| Service | Container | Host port | Role |
|---|---|---|---|
| `llm-agent` | `llm-agent-dev` | **3003** | Flask app + chat UI + LangChain agent |
| `sidecar` | `agent-id-sidecar-dev` | *none* | Microsoft Entra SDK — issues tokens |
| `weather-api` | `weather-api-dev` | *none* | Downstream API, validates JWT on every request |
| `ollama` | `ollama-dev` | *none* | Local LLM — only used in Ollama mode |

> **Security note:** Only the UI is exposed to the host. The sidecar, weather-api and Ollama are reachable only within the Docker network, per [Microsoft's trust-boundary guidance](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/security).

---

## 9. Running the tests

Unit tests cover JWT decode, debug logging, all Flask routes, input validation, city extraction, and LangChain agent creation.

```bash
cd sidecar/dev
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v
```

Expected: **28 passed in ~4s, zero warnings**.

---

## 10. LangChain version and architecture

| Package | Pinned | Role |
|---|---|---|
| `langchain` | `>=1.0.0` | Hosts `create_agent` (LangGraph ReAct builder) |
| `langchain-core` | `>=1.0.0` | `@tool` decorator, message types |
| `langchain-ollama` | `>=1.0.0` | `ChatOllama` provider |
| `langgraph` | `>=1.0.0` | Underlying agent runtime |

The agent is a **LangGraph ReAct agent** built with [`langchain.agents.create_agent`](https://docs.langchain.com/oss/python/langchain/agents) — this is the current pattern as of LangChain 1.x. The older `AgentExecutor` / `langgraph.prebuilt.create_react_agent` paths are deprecated and no longer used.

---

## 11. Troubleshooting

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

## 12. Stop & cleanup

```bash
# Stop containers, keep volumes/images
docker compose down

# Also remove the Ollama model cache
docker compose down -v

# Nuke everything (containers, volumes, images)
docker compose down -v --rmi all
```

---

## 13. Files

```
sidecar/dev/
├── app.py               # Flask app + LangGraph ReAct agent + sidecar client
├── docker-compose.yml   # llm-agent, sidecar, weather-api, ollama
├── Dockerfile           # Python 3.11 slim base
├── requirements.txt     # LangChain 1.x, Flask, MSAL
├── .env.example         # Template — copy to .env
├── templates/
│   └── index.html       # Chat UI, MSAL.js, token trace panel
└── tests/
    ├── __init__.py
    └── test_app.py      # 28 pytest tests
```

---

## 14. References

- [Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/identity-platform/agent-identity/)
- [Microsoft Entra SDK auth sidecar (container image)](https://mcr.microsoft.com/en-us/product/entra-sdk/auth-sidecar/about)
- [LangChain Agents (v1)](https://docs.langchain.com/oss/python/langchain/agents)
- [microsoft-identity-web Client Credentials](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)
