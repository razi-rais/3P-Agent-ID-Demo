# Architecture summary

One Azure Container App, four containers on shared `localhost`:

| Container | Role |
|---|---|
| `llm-agent` | Flask + LangChain, port 3000; calls AWS Bedrock via `boto3` using `AWS_WEB_IDENTITY_TOKEN_FILE=/azure-token/token` |
| `sidecar` | `mcr.microsoft.com/entra-sdk/auth-sidecar`, localhost:5000; mints Agent Identity tokens using `SignedAssertionFromManagedIdentity` |
| `weather-api` | Validates Agent Identity JWT (JWKS, iss, aud, appid); returns real Open-Meteo data |
| `token-refresher` | Every ~50 min, reads MI assertion from IMDS, exchanges at Entra `/oauth2/v2.0/token` for v1 JWT, writes `/azure-token/token` |

**Two federation chains, one managed identity:**

- **Chain A (MI → Entra Blueprint):** direct. Audience stays `api://AzureADTokenExchange`. Sidecar signs assertions.
- **Chain B (MI → AWS STS):** via v1 token exchange through an intermediary Entra app. See [v1-token-exchange.md](./v1-token-exchange.md).

**Trust anchors (all three pinned in AWS IAM role trust policy):**

| JWT claim | Value |
|---|---|
| `iss` | `https://sts.windows.net/<tenant>/` (v1 issuer, trailing slash) |
| `aud` | `api://<intermediary-app-id>` |
| `sub` | `<intermediary-app-SP-object-id>` |

**What rotates:** every JWT ≤ 1 h; AWS STS creds ≤ 1 h. **What's permanent:** OIDC provider, role trust, federated credentials.

Full detail: tutorial [§1.2](../../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#12-architecture).
