# Architecture summary

One Azure Container App, four containers on shared `localhost`:

| Container | Role |
|---|---|
| `llm-agent` | Flask + LangChain, port 3000; calls local Ollama for completions |
| `sidecar` | `mcr.microsoft.com/entra-sdk/auth-sidecar`, localhost:5000; signs Entra assertions using `SignedAssertionFromManagedIdentity` (no client secret) |
| `weather-api` | Validates Agent Identity JWT (JWKS, iss, aud, appid) on every request |
| `ollama` | Local LLM server on localhost:11434; model baked into image (recommended) or pulled at runtime |

**One federation chain, one direction:**

```
MI (system-assigned) ──FIC──▶ Blueprint app ──▶ Graph + weather-api
                                 (aud = api://AzureADTokenExchange)
```

**What's NOT in this deployment** (compared to the AWS variant):
- No intermediary Entra app
- No AWS OIDC identity provider
- No IAM role trust policy
- No token refresher container
- No `AWS_*` or `BEDROCK_*` env vars
- No shared `EmptyDir` volume for JWT passing

**What rotates:** Agent Identity tokens (minutes), MI JWTs (~24 h). **What's permanent:** Blueprint federated credential. **What's local and never rotates:** Ollama weights baked into the image.

Full detail: tutorial [§1.2](../../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md#12-architecture).
