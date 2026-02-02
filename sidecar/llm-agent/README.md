# 3P Agent Identity Demo

A visual demonstration of how AI agents use **Microsoft Entra Agent Identity** tokens to securely call APIs.

> **📚 Prerequisites:** New to Agent Identity? Start with the [Sidecar Guide](../SIDECAR-GUIDE.md) to understand the fundamentals using simple PowerShell commands. This demo builds on those concepts with a complete end-to-end example.

---

## High-Level Overview

```
                                    ┌─────────────────┐
                                    │   Microsoft     │
                                    │   Entra ID      │
                                    │                 │
                                    └────────▲────────┘
                                             │
                                      2. Get Agent ID
                                           Token
                                             │
┌──────────┐  1. "What is weather   ┌───────┴───────┐                         ┌─────────────┐
│          │      in Dallas?"       │               │  3. Call API            │             │
│   User   │  ──────────────────▶   │   LLM Agent   │  ──────────────────▶    │ Weather API │
│          │                        │   + Sidecar   │    + Agent ID Token     │             │
│          │  ◀──────────────────   │               │  ◀──────────────────    │             │
└──────────┘  5. "Dallas: 75°F,     └───────────────┘  4. Weather Data        └─────────────┘
                  Sunny"
```

**The Flow:**
1. **User asks a question** → "What is the weather in Dallas?"
2. **Agent gets Agent ID token** → Sidecar requests JWT from Entra ID
3. **Agent calls API with token** → Weather API receives `Authorization: Bearer <Agent ID Token>`
4. **API validates & responds** → Checks token signature, returns weather data
5. **User gets answer** → "Dallas: 75°F, Sunny"

**Key Concept:** The agent authenticates as itself (not as a user) using its own identity, enabling secure machine-to-machine API calls.

---

## Detailed Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Docker Network                                     │
│                                                                                 │
│  ┌────────────────────┐                          ┌────────────────────┐         │
│  │                    │                          │                    │         │
│  │     LLM Agent      │   GET /token?AgentId=... │      Sidecar       │         │
│  │    ┌──────────┐    │ ────────────────────────▶│  ┌──────────────┐  │         │
│  │    │ Flask UI │    │                          │  │ Entra SDK    │  │         │
│  │    │ Chat     │    │ ◀────────────────────────│  │ Token Cache  │  │         │
│  │    │ Debug    │    │   { authorizationHeader: │  └──────┬───────┘  │         │
│  │    └──────────┘    │     "Bearer eyJ..." }    │         │          │         │
│  │                    │                          │         │          │         │
│  │    Port 3000       │                          │   Port 5001        │         │
│  └─────────┬──────────┘                          └─────────┼──────────┘         │
│            │                                               │                    │
│            │                                               │                    │
│            │ GET /weather?city=seattle                     │ POST /oauth2/token │
│            │ Authorization: Bearer eyJ...                  │ client_credentials │
│            │                                               │                    │
│            ▼                                               ▼                    │
│  ┌────────────────────┐                          ┌────────────────────┐         │
│  │                    │                          │                    │         │
│  │    Weather API     │                          │    Microsoft       │         │
│  │  ┌──────────────┐  │                          │    Entra ID        │         │
│  │  │ Validates    │  │                          │  ┌──────────────┐  │         │
│  │  │ JWT Token    │  │                          │  │ Issues JWT   │  │         │
│  │  │ - Signature  │  │                          │  │ with claims: │  │         │
│  │  │ - Expiry     │  │                          │  │ - appid      │  │         │
│  │  │ - Claims     │  │                          │  │ - oid        │  │         │
│  │  └──────────────┘  │                          │  │ - roles      │  │         │
│  │                    │                          │  └──────────────┘  │         │
│  │    Port 8080       │                          │                    │         │
│  └────────────────────┘                          └────────────────────┘         │
│                                                                                 │
│  ┌────────────────────┐                                                         │
│  │      Ollama        │  ◀── Optional: Local LLM for agentic tool calling       │
│  │   (qwen2.5:1.5b)   │      Only used in LangChain Mode                        │
│  │    Port 11434      │                                                         │
│  └────────────────────┘                                                         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Token Flow (Step by Step)

```
User Query: "What's the weather in Seattle?"
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 1. LLM Agent receives query             │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 2. Agent requests token from Sidecar    │
│    GET /AuthorizationHeaderUnauthenticated/graph?AgentIdentity={id}
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 3. Sidecar gets JWT from Entra ID       │
│    - Contains: appid, oid, roles, tid   │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 4. Agent calls Weather API with token   │
│    Authorization: Bearer <JWT>          │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 5. Weather API validates token          │
│    - Checks signature, expiry, claims   │
│    - Returns weather data               │
└─────────────────────────────────────────┘
```

## Two Modes

| Mode | Speed | Description |
|------|-------|-------------|
| **⚡ Direct Mode** | ~1-2s | Calls weather tool directly, skips LLM. Best for demos. |
| **🔗 LangChain Mode** | ~10-30s | LLM decides when to call tools. Requires Ollama. |

## Quick Start

```powershell
# From the sidecar folder
cd sidecar

# Start all services
docker-compose up -d

# Open the demo
Start-Process "http://localhost:3000"
```

## Screenshots

### 1. Initial UI - Ready for Demo

![Demo UI](./docs/images/demo-ui.png)

The demo starts with a clean interface explaining both modes:
- **Left Panel:** Chat interface with mode toggle and status indicators
- **Right Panel:** Token flow debug panel (waiting for queries)

---

### 2. Complete Token Flow - Weather Query

![Demo Flow](./docs/images/demo-flow.png)

After asking "Weather in Dallas?", the debug panel shows the complete flow:
1. **START** - Query received
2. **DIRECT CALL** - Tool function invoked
3. **TOKEN REQUEST** - Sidecar URL with Agent Identity
4. **TOKEN RECEIVED** - JWT claims displayed (appid, oid, tid, roles)
5. **WEATHER API** - API call with Authorization header
6. **WEATHER RESPONSE** - Real-time data from Open-Meteo API
7. **COMPLETE** - Result returned to user

---

### 3. JWT Claims & API Response Details

![Token Details](./docs/images/token-details.png)

Detailed view showing:
- **JWT Claims:** `appid`, `oid`, `tid`, `iss`, `aud`, `roles`, `exp`, `iat`
- **Weather Response:** Real-time data including temperature, humidity, wind speed, timezone, and timestamp
- **Authentication:** Validated by Agent Identity Token with Agent App ID

### Sample Run - Direct Mode

**User:** "What's the weather in Seattle?"

**Debug Output:**
```
✅ 0. START
   Processing query (no LLM): What's the weather in Seattle?

✅ 1. DIRECT CALL  
   Calling weather function directly for: seattle

✅ 2. TOKEN REQUEST
   Sidecar URL: http://sidecar:5000/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=<your-agent-app-id>

✅ 2. TOKEN RECEIVED
   Got Agent Identity token from sidecar
   {
     "jwt_claims": {
       "aud": "https://graph.microsoft.com",
       "iss": "https://sts.windows.net/...",
       "app_displayname": "AgentID-Demo-Agent",
       "appid": "<your-agent-app-id>",
       "roles": [],
       "tid": "<your-tenant-id>"
     }
   }

✅ 3. WEATHER API
   Calling Weather API for: seattle

✅ 3. WEATHER RESPONSE
   Got weather data from API
   {
     "city": "Seattle",
     "temperature": 52,
     "condition": "Cloudy",
     "humidity": 78,
     "validated_by": "Agent Identity Token"
   }

✅ 5. COMPLETE
   Query processed (direct mode)
```

**Response:**
```
Weather for Seattle:
- Temperature: 52°F
- Condition: Cloudy
- Humidity: 78%
- Authentication: Validated by Agent Identity Token
- Agent App ID: <your-agent-app-id>

✅ This data was securely retrieved using Agent Identity authentication!
```

## JWT Token Claims

The Agent Identity token contains these key claims:

| Claim | Description |
|-------|-------------|
| `appid` | The Agent's Application ID (Client ID) |
| `oid` | Object ID of the service principal |
| `tid` | Tenant ID |
| `aud` | Audience (the API being called) |
| `roles` | Assigned application roles |
| `app_displayname` | Friendly name of the agent app |

## Services

| Service | Port | Description |
|---------|------|-------------|
| `llm-agent` | 3000 | Flask app with chat UI |
| `sidecar` | 5001 | Microsoft Entra SDK for Agent Identity |
| `weather-api` | 8080 | Sample API that validates tokens |
| `ollama` | 11434 | Local LLM (optional, for LangChain mode) |

## Environment Variables

Set in `.env` file:

```env
TENANT_ID=your-tenant-id
BLUEPRINT_APP_ID=your-blueprint-app-id
BLUEPRINT_CLIENT_SECRET=your-secret
AGENT_CLIENT_ID=your-agent-app-id
```

## Stop & Cleanup

```powershell
# Stop all containers (keeps images and volumes)
docker-compose down

# Stop and remove volumes (clears Ollama model cache)
docker-compose down -v

# Stop, remove volumes, AND remove images (full cleanup)
docker-compose down -v --rmi all

# Remove just the Ollama model cache (if needed)
docker volume rm sidecar_ollama_data
```

## Files

```
llm-agent/
├── app.py           # Main Flask application
├── requirements.txt # Python dependencies
├── Dockerfile       # Container build
└── README.md        # This file
```
