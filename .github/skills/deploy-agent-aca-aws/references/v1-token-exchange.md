# Why the AWS leg needs a v1 token exchange

## Symptom

```
botocore.exceptions.ClientError: An error occurred (InvalidIdentityToken)
when calling the AssumeRoleWithWebIdentity operation: Incorrect token audience
```

…even when the AWS OIDC identity provider's client-ID list contains the audience from the MI's token.

## Root cause

Azure Container Apps' system-assigned managed identity gets tokens from IMDS with:

- `iss = https://login.microsoftonline.com/<tenant>/v2.0`
- `aud = fb60f99c-7a34-4190-8149-302f77469936` (Microsoft's first-party `AzureADTokenExchange` GUID)

AWS STS validates `aud` against the OIDC provider's client-ID list and **rejects the GUID form** regardless. This is a known AWS behavior, not a config error.

## Supported fix

Insert an **intermediary Entra app** whose tokens AWS STS accepts:

1. Create an Entra app with `identifierUris = ["api://<self-appId>"]` (tenant policies usually block custom `api://` URIs, self-ID always works).
2. `PATCH /applications(appId='...') { "api": { "requestedAccessTokenVersion": 1 } }`.
3. Add a **federated identity credential** on the intermediary app with `subject = <MI_OBJECT_ID>`, `audience = api://AzureADTokenExchange`.
4. The token refresher calls `POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`:
   - `grant_type=client_credentials`
   - `client_id=<intermediary-app-id>`
   - `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
   - `client_assertion=<MI assertion from IMDS for api://AzureADTokenExchange>`
   - `scope=api://<intermediary-app-id>/.default`
5. Response is a v1 token: `iss = https://sts.windows.net/<tenant>/`, `aud = api://<intermediary-app-id>`, `sub = <intermediary-app-SP-OID>`.
6. AWS OIDC provider URL is the v1 issuer **with trailing slash**: `https://sts.windows.net/<tenant>/`. IAM role trust pins `aud` and `sub`.

Refresher implementation: [sidecar/aws/azure-token-refresher/refresh.py](../../../../sidecar/aws/azure-token-refresher/refresh.py).

## Why this is a "token shape adapter", not a real trust change

The intermediary app has:

- **No client secret, no certificate.** It can only mint tokens when the MI hands over a federated assertion.
- **No downstream permissions.** AWS doesn't care what the app *can do* in Entra — only the shape and claims of the token it issues.

The actual trust is still "MI ↔ AWS role". The intermediary app is a stateless JWT re-encoder.

## Common misconfigurations

- **Forgetting the trailing slash** on the OIDC provider URL (`sts.windows.net/<tenant>/` not `.../v2.0`). AWS treats them as different providers.
- **Using `identifierUris = ["api://my-name"]`**. Tenant policy rejects this in many tenants. Use `api://<self-appId>`.
- **Omitting `requestedAccessTokenVersion: 1`**. The app issues v2 tokens by default; AWS rejects them.
- **Audience mismatch in role trust condition.** Must match exactly — `api://<app-id>` with the GUID, not a friendly URI.
