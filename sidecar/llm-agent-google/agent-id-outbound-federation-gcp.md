# Microsoft Entra Agent ID: Outbound Federation Into GCP

## 1. What this article covers

This article is a detailed walkthrough of how Microsoft Entra Agent ID can be federated outbound from Azure into Google Cloud. Cross-hyperscaler agentic workloads are a common enterprise pattern where the same agent needs to authenticate to services in more than one cloud. You will learn how to use Entra Agent ID from Azure into GCP, in particular into Vertex AI (which became part of the Gemini Enterprise Agent Platform, or GEAP, in April 2026). To understand the architecture, here is the deployment this article walks: the Weather Agent runs **securely on Azure Container Apps** under a managed identity, and the **agentic LLM capability comes from Google** in the form of a Gemini model on Vertex AI. The Weather Agent calls Gemini under the same Agent Identity it uses for Azure, with no service-account key on disk, making the deployment completely secretless. The article uses the name "Vertex AI" throughout because that is the name still carried on the wire by the GCP APIs, IAM role names (`roles/aiplatform.user`), audit log resources (`aiplatform.googleapis.com`), and the GCP SDKs.

The Weather Agent runs on Azure Container Apps under one Microsoft Entra Agent Identity, with no client secret and no GCP service-account key on disk. The same Agent Identity reaches both sides: the Weather API on Azure accepts the Agent Identity JWT directly, and Vertex AI on GCP accepts a short-lived Google access token obtained by exchanging the same JWT at the GCP STS through Workload Identity Federation.

We will use the [Microsoft Entra SDK for Agent ID](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/overview) to acquire Agent Identity tokens from the Microsoft Entra token endpoint (Microsoft Entra STS). The SDK is shipped as a container that runs as a sidecar alongside the agent and exposes a small HTTP API. The agent code calls that local HTTP API for every token, so it never has to talk to Microsoft Entra directly or hold any client secret. The architecture pattern walked in this article does not depend on which client library mints the token. What matters is that the token reaching GCP is an Agent Identity JWT minted by Microsoft Entra under the Blueprint, and that contract is the same no matter how the token is procured. The walkthrough assumes you are already comfortable with Microsoft Entra Agent ID, the Agent Identity Blueprint, and the Federated Identity Credential (FIC), so if any of those are new, it is worth skimming [Agent identities](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities) and [Agent identity blueprints](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-blueprint) before continuing.


We will start with the first building block, the piece that glues Azure and GCP together in this architecture: Workload Identity Federation.

## 2. The WIF contract from scratch

GCP [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) is a contract between your GCP project and an outside identity provider like Microsoft Entra. Once the contract is in place, GCP agrees to accept a token from that outside provider and, after validating it, hand back a federated credential that can impersonate a named GCP service account (SA). The contract has two sides. Entra is the issuer, and GCP is both the verifier (it trusts that issuer) and the impersonator (it allows impersonation of a chosen SA). Together they define a single end-to-end path from the Entra tenant to GCP (for example, Vertex AI), and the issuer, the audience, the subject, and the impersonation grant all have to line up before any token is accepted. The end goal is to configure an Entra Agent Identity as the subject GCP will accept. Let us walk through what makes up that contract.

**Figure 1. The WIF contract: the trust agreement between Entra and GCP. Box numbers match the table below.**

```
   ╔══════════════════════════════════════════════════════════════════╗
   ║                       WIF contract anatomy                       ║
   ║              (one trust contract, two sides of it)               ║
   ╚══════════════════════════════════════════════════════════════════╝

     ENTRA SIDE  (issuer)            GCP SIDE  (verifier + impersonator)
     ────────────────────            ────────────────────────────────────

     ┌───────────────────┐               ┌───────────────────────┐
     │ (1) Entra v2 IdP  │               │ (3) Workload Identity  │
     │  login.microsoft  │               │          Pool          │
     │     /v2.0         │               │ (env-scoped namespace) │
     └─────────┬─────────┘               └───────────┬────────────┘
               │ mints                               │ contains
               ▼                                     ▼
     ┌───────────────────┐               ┌────────────────────────┐
     │ (2) Signed JWT    │ ── cross ──►  │ (4) Pool Provider      │
     │  iss / aud / sub  │    cloud      │      issuerUri         │
     │  presented as     │   subject_    │      allowedAudiences  │
     │  subject_token in │    token      │      attributeMapping  │
     │  STS token-exch.  │               │      attributeCondition│
     └───────────────────┘               └───────────┬────────────┘
                                                     │ google.subject = X
                                                     ▼
                                         ┌────────────────────────┐
                                         │ (5) IAM binding on SA  │
                                         │   roles/iam.           │
                                         │     workloadIdentity…  │
                                         │   principal://…/X      │
                                         └───────────┬────────────┘
                                                     │ allows impersonate
                                                     ▼
                                         ┌────────────────────────┐
                                         │ (6) Service Account    │
                                         │   IAM roles attached:  │
                                         │     aiplatform.user    │
                                         └───────────┬────────────┘
                                                     │ (7) ya29.* access token
                                                     ▼
                                         ┌────────────────────────┐
                                         │ (8) Vertex AI / GEAP   │
                                         └────────────────────────┘
```

The table below walks the moving parts in the diagram above. For each one it names the part, says which side of the contract it sits on, what kind of thing it actually is, gives a short description, and links to the canonical doc. Tenant ids, project ids, and principal GUIDs are intentionally left out here. Those values are introduced in the deployment sections and walkthrough that follow.

| # | Side | Item | What it really is | Description | Canonical docs |
|---|---|---|---|---|---|
| 1 | Entra | Entra v2 IdP | An OIDC issuer endpoint | The OpenID Connect issuer that signs every JWT in this article. Its issuer URL is the tenant's v2 endpoint (`https://login.microsoftonline.com/<tenant>/v2.0`). You do not create this object. It is the tenant itself, accessed through the v2 OIDC endpoint. | [OpenID Connect on the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc) |
| 2 | Entra | Signed JWT (subject_token) | A token / claim carrier | The RS256-signed JWT Entra mints for the caller. Carries `iss`, `aud`, and `sub` (plus the claims walked in the wire-shape section below). This JWT is what becomes the `subject_token` form field in the RFC 8693 token-exchange POST to GCP STS. | Minted by `/oauth2/v2.0/token`. The wire-level shape is detailed below. |
| 3 | GCP | Workload Identity Pool | A resource (a GCP IAM resource) | A container in your GCP project that holds one or more trust statements with outside IdPs. Environment-scoped namespace (dev vs staging vs prod). | [Manage pools and providers](https://cloud.google.com/iam/docs/manage-workload-identity-pools-providers) |
| 4 | GCP | Pool Provider | A resource (a GCP IAM resource) | A single trust statement inside the pool. Declares the OIDC issuer URL, the allowed audiences, an `attributeMapping`, and an `attributeCondition` written in [Common Expression Language](https://cloud.google.com/iam/docs/workload-identity-federation#attribute-conditions). The pinning lives here. | [Configure WIF with OIDC](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-providers) |
| 5 | GCP | IAM binding | A policy entry | Entry on the SA's IAM policy that grants `roles/iam.workloadIdentityUser` to a principal name of the form `principal://iam.googleapis.com/projects/<project-number>/locations/global/workloadIdentityPools/<pool>/subject/<sub-value>`. This is the final gate. | [Impersonate a service account](https://cloud.google.com/iam/docs/use-service-accounts#impersonating-service-accounts) |
| 6 | GCP | Service Account | A resource (a principal) | The GCP-side principal whose permissions the agent borrows. Holds the IAM roles that govern the downstream API (here `roles/aiplatform.user` for Vertex AI). The SA has no key file. Its credentials only exist as the federated token the WIF exchange produces. | [Service accounts overview](https://cloud.google.com/iam/docs/service-account-overview) |
| 7 | GCP | Federated access token (`ya29.*`) | A token | The OAuth 2.0 access token returned by `iamcredentials.googleapis.com:generateAccessToken` after WIF and impersonation both succeed. This is the bearer the agent attaches to the Vertex AI call. | Returned by `sts.googleapis.com` (token exchange) and `iamcredentials.googleapis.com` (impersonation). |
| 8 | GCP | Vertex AI / GEAP | A service | The downstream API the agent ultimately calls. Validates the `ya29.*` bearer and checks the SA holds the required `aiplatform.*` permissions. | [Vertex AI documentation](https://cloud.google.com/vertex-ai/docs) (rebranded under the Gemini Enterprise Agent Platform on April 22, 2026). |


A few things are worth calling out:

- **Agent Identity** (Entra side) is the subject of the contract. Its OID lands in the `sub` claim of the Agent Identity JWT, and that OID is the value the GCP provider's `attributeCondition` pins on. Everything else in this list exists to receive, verify, and authorize impersonation against that one identity.
- **Pool** answers "which environment is this trust relationship for?" Google [recommends](https://cloud.google.com/iam/docs/best-practices-for-using-workload-identity-federation) creating a new pool for each non-Google Cloud environment that needs to access Google Cloud resources, such as development, staging, or production. So the pool is environment-scoped, not workload-scoped.
- **Provider** answers "which outside identity provider is this trust statement about, and which tokens from that provider should be considered?" The pinning lives here.
- **Service account** answers "once a token is considered valid, what permissions does the federated principal (the Agent Identity) get on the GCP side?" The IAM roles live on the SA, not on the provider.
- **IAM binding** answers "which specific outside identity is allowed to impersonate this service account?" The binding is the final gate.

So how does this work in practice, when an Agent Identity JWT is presented to GCP STS?

**Figure 2. The trust-gate funnel: every gate a token must pass before GCP issues a federated access token.**

```
                          Trust gate funnel
                          ─────────────────

   JWT in
      │
      ▼
   1. Signature valid for provider's issuerUri?           ─ no ─►  FAIL
      (Entra v2 JWKS, RS256)
      │ yes
      ▼
   2. aud claim in provider's allowedAudiences list?      ─ no ─►  FAIL
      (the audience pun bites here)
      │ yes
      ▼
   3. attributeCondition CEL returns true?                ─ no ─►  FAIL
      (assertion.sub == "<Agent Identity OID>")
      │ yes
      ▼
   4. attributeMapping produces google.subject = X
      │
      ▼
   5. SA IAM policy grants                                ─ no ─►  FAIL
      roles/iam.workloadIdentityUser
      to principal://.../subject/X
      │ yes
      ▼
   6. iamcredentials:generateAccessToken                  ────────►  PASS (ya29.*)
```

The trust hierarchy runs top to bottom. Each gate maps to one numbered box in Figure 2.

1. **Signature.** GCP STS validates the JWT's signature against the JWKS published at the provider's `issuerUri`, discovered through [OpenID Connect metadata](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc) at `<issuerUri>/.well-known/openid-configuration`. For Entra v2 the algorithm is RS256. ([GCP: Configure WIF with OIDC](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-providers))
2. **Audience.** The `aud` claim in the JWT has to appear in the provider's [`allowedAudiences`](https://cloud.google.com/iam/docs/reference/rest/v1/projects.locations.workloadIdentityPools.providers#oidc) list. For an Agent Identity JWT, `aud` carries the **Blueprint's appId GUID** (for example `bbbbbbbb-...`), because the Blueprint is the Entra app registration that hosts the Agent Identity and is therefore the resource the token is scoped to. This gate says "the token came from a Blueprint I trust." The URI form (`api://<Blueprint>/.default`) versus bare GUID detail is covered below.
3. **Attribute condition.** The provider's [`attributeCondition`](https://cloud.google.com/iam/docs/workload-identity-federation#attribute-conditions) is a [CEL](https://github.com/google/cel-spec) boolean expression that has to evaluate to `true` against the incoming claims. A typical condition is `assertion.sub == "aaaaaaaa-..."`, where the value is the Entra `objectId` of the [Agent Identity](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities) service principal hosted by the Blueprint. The `sub` claim carries that OID, so the condition pins to a single Agent Identity. Audience (#2) narrows the gate to one Blueprint. Subject (#3) narrows it further to one Agent Identity inside that Blueprint.
4. **Attribute mapping.** The provider's [`attributeMapping`](https://cloud.google.com/iam/docs/workload-identity-federation#mappings) copies claims out of the JWT into Google-side attributes. The minimum mapping is `google.subject = assertion.sub`, which feeds the Agent Identity OID into the [federated principal name](https://cloud.google.com/iam/docs/principal-identifiers#workload-pools) `principal://iam.googleapis.com/projects/<project-number>/locations/global/workloadIdentityPools/<pool>/subject/<Agent Identity OID>`.
5. **IAM binding.** The IAM policy on the requested service account has to grant [`roles/iam.workloadIdentityUser`](https://cloud.google.com/iam/docs/workload-identity-federation#impersonation) to that principal name. This is the binding that authorizes the Agent Identity to impersonate this specific service account.
6. **Token issuance.** Once gates 1-5 all pass, [`iamcredentials.googleapis.com:generateAccessToken`](https://cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/generateAccessToken) returns a federated `ya29.*` access token. The `ya29.` prefix is Google's internal convention for opaque [OAuth 2.0 bearer access tokens](https://cloud.google.com/docs/authentication/token-types#access). The token is **not a JWT**, the caller cannot decode its contents. It is short-lived (1-hour default), bound to the impersonated service account, and the agent attaches it as `Authorization: Bearer ya29....` on every Vertex AI call.

Any one of the gates failing produces a different error, and which error you see tells you where to look. The failure modes are walked in the failure-modes section below.


There is one detail about how Entra mints audiences that is worth covering before moving on, because it is responsible for the most common "WIF rejects my token" failure mode. The audience the Entra SDK **asks for** in its token request and the audience that **appears** in the issued JWT's `aud` claim are not always the same string.

Entra app registrations have two equivalent identifiers: the **appId GUID** (immutable, always present, of the form `bbbbbbbb-...`) and the **identifier URI** (an alias the developer assigns, conventionally `api://<appId>`). Either form can be used to address the app as a token audience. The Entra SDK in this article uses the URI form, `scope=api://<Blueprint>/.default`, where `<Blueprint>` is the [Agent Identity Blueprint](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-blueprint), the Entra app registration that hosts the Agent Identity. The `<resource>/<permission>` shape is the OAuth 2.0 [scope](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc) parameter convention. `api://<Blueprint>` is the resource (the Blueprint itself), and [`/.default`](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc#the-default-scope) is the pseudo-scope that the [client_credentials grant flow requires](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc#client-credentials-grant-flow-and-default), telling Entra to issue a token carrying every app role already granted to this caller for that resource.

Entra does not stamp the URI into the issued token. It normalizes the URI to the underlying appId GUID and writes the GUID into the `aud` claim. The URI form never appears on the wire. Base64-decoding any Agent Identity JWT confirms this: `aud` is always a bare GUID.

GCP STS validates the audience with a byte-for-byte string comparison of `aud` against the provider's `allowedAudiences` list, with no URI-to-GUID resolution on the GCP side. A provider configured with `allowedAudiences=["api://bbbbbbbb-..."]` therefore rejects every Agent Identity JWT, because the wire value is `bbbbbbbb-...`, which is not byte-equal to `api://bbbbbbbb-...`. The fix is to list **both** forms in `allowedAudiences`. The GUID form is what Entra actually emits today, and the URI form is there for safety in case the normalization behavior changes or a different client library asks for a different scope shape.

## 3. Pool and provider topology

When more than one Agent Identity in your tenant needs to call into the same GCP project, the topology question is whether each Agent Identity gets its own pool, its own provider inside a shared pool, or its own service account behind a shared provider. The deployment captured for this article uses one pool, one provider, and one service account, because the deployment has one Agent Identity. The same pool, however, is the right place to add a second provider the day you add a second Agent Identity that should be distinguishable to GCP.

Google's [own recommendation](https://cloud.google.com/iam/docs/best-practices-for-using-workload-identity-federation#use-different-pools-for-different-environments) on pools, verbatim from the best-practices page, is:

> "In general, we recommend creating a new pool for each non-Google Cloud environment that needs to access Google Cloud resources, such as development, staging, or production environments."

So Google's heuristic is **pool equals environment**, not pool equals IdP type and not pool equals workload. With that heuristic in mind, the three working topologies for "one or more Agent Identities federating into the same project" are summarized below.

| Option | Approach | Pool | Providers | When to use |
|---|---|---|---|---|
| A | One Agent Identity, one provider, one SA | one | 1 | The default. Single Agent Identity federating into one project. |
| B | Multiple Agent Identities, same pool, one provider per OID | one | N (one per Agent Identity OID) | Two or more Agent Identities that need to be distinguishable on the GCP side. Each provider pins on a different OID. |
| C | Multiple Agent Identities, different pools | N pools | 1 per pool | Cross-environment isolation (dev vs prod), cross-team isolation, or when you need a hard pool-level kill switch. |

The deployment in this article uses Option A. A single Agent Identity (`Weather Agent (GCP ACA)`) federates from one Entra tenant into one GCP project. The quota of 100 providers per pool is far enough above what most deployments will hit that you can grow into Option B without restructuring.

**Figure 3. The single-Agent-Identity topology used throughout this deployment.**

```
                      (1) Workload Identity Pool
                     (one pool, environment-scoped)
                                   │
                                   ▼
                       (2) Agent-Identity-pinned
                              provider

                       issuer:    Entra v2
                       audience:  Blueprint identifier
                       condition: sub == Agent Identity OID
                                   │
                                   ▼
                       (3) Service Account for
                           the agent workload
                           roles/aiplatform.user
                                   │
                                   ▼
                            (4) Vertex AI
```

A naming convention is worth being explicit about, because it pays off in operations. Name the provider so it tells you which Agent Identity (or Blueprint) it pins on, so anyone reading the pool listing can tell at a glance which Entra principal each provider trusts. Name the service account after the **workload purpose** (which agent, which model), so anyone reading the GCP IAM policy or any per-resource binding knows what the SA is for without cross-referencing. The actual names used in this deployment appear in the GCP-bootstrap section below.

---

## 4. Entra-side bootstrap

The Entra-side work in this article involves four objects: a UAMI attached to the Azure Container Apps revision, a Blueprint, an Agent Identity hosted by the Blueprint, and a federated identity credential (FIC) on the Blueprint that lets the UAMI act as the client credential.

The UAMI is the runtime trust anchor. It is what Azure Container Apps uses to prove to Entra that the request really is coming from inside the container app, with no key material on disk to manage or rotate. The UAMI is not the principal GCP sees. It exists only so that the Entra SDK can perform the client_credentials grant against Entra without a client secret. The principal GCP sees is the Agent Identity, whose OID lands in the `sub` claim of the token the Entra SDK returns.

**Figure 4. Entra-side object graph: the UAMI anchors the FIC, the Blueprint hosts the Agent Identity, and the Agent Identity JWT is what GCP STS receives.**

```
            ┌─────────────────────────────────┐
            │ (1) UAMI:                       │
            │     mi-weather-agent-agentid    │
            │     principalId = cccccccc-…    │
            │  (attached to ACA, FIC anchor)  │
            └────────────────┬────────────────┘
                             │ assertion via ACA MI endpoint
                             ▼
            ┌─────────────────────────────────┐
            │ (2) FIC on Blueprint bbbbbbbb-… │
            │     subject = cccccccc-…        │
            │     aud     = api://AzureAD…    │
            └────────────────┬────────────────┘
                             │ client_credentials grant (FIC)
                             ▼
            ┌─────────────────────────────────┐
            │ (3) Blueprint bbbbbbbb-…        │
            │     hosts                       │
            │     Agent Identity              │
            │     oid = aaaaaaaa-…            │
            └────────────────┬────────────────┘
                             │ acquire_token_for_client
                             │ (FMI Path = Agent ID OID)
                             ▼
            ┌─────────────────────────────────┐
            │ (4) Agent Identity JWT          │
            │     iss = Entra v2              │
            │     sub = oid = appid = aaaaaaaa│
            │     xms_par_app_azp = bbbbbbbb-…│
            └─────────────────────────────────┘
```

Before the step-by-step, it is worth pinning down a detail that catches a fair number of first deployments. On a normal Azure VM, the managed identity token endpoint lives at `http://169.254.169.254/metadata/identity/oauth2/token` (the IMDS link-local address). On **Azure Container Apps**, the endpoint is different. ACA injects two environment variables, `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`, into every container in the pod. The endpoint URL is something like `http://localhost:12356/msi/token`, and the port is dynamic per replica. The expected header is `X-IDENTITY-HEADER: <value of IDENTITY_HEADER>`, which is a per-replica secret that ACA injects to prevent any other container on the host from impersonating the identity. The Azure SDK's `ManagedIdentityCredential` knows the difference between the two endpoint shapes and picks the right one based on which env vars are present. The detail matters when something is not working and you reach for `curl` to probe the endpoint, because hitting the VM IMDS URL from inside an ACA pod times out and reports nothing useful.

The full Entra-side bootstrap sequence is below.

| Step | What you do | Command | Verified value |
|---|---|---|---|
| 1 | Create the Blueprint app registration | `az ad app create --display-name "GCP ACA Blueprint" --sign-in-audience AzureADMyOrg` | `appId=bbbbbbbb-1111-2222-3333-444444444444` |
| 2 | Create the Agent Identity service principal under the Blueprint | (Microsoft Graph call, see the linked Microsoft Learn page) | `oid=aaaaaaaa-1111-2222-3333-444444444444` |
| 3 | Create the UAMI that will act as the FIC anchor | `az identity create --resource-group rg-agent-gcp-prod --name mi-weather-agent-agentid` | `clientId=eeeeeeee-...`, `principalId=cccccccc-...` |
| 4 | Attach the UAMI to the container app | `az containerapp identity assign --user-assigned <uami-id> -g rg-agent-gcp-prod -n agent-app-agentid` | `identity.userAssignedIdentities[...]` populated |
| 5 | Register a federated identity credential on the Blueprint, pinned to the UAMI's `principalId` | `az ad app federated-credential create --id bbbbbbbb-... --parameters '{...}'` (see snippet below) | (none, returns 201) |

The FIC payload, with synthetic identifiers in place of the live deployment values, is:

```json
{
  "name": "uami-mi-weather-agent-agentid",
  "issuer": "https://login.microsoftonline.com/11111111-aaaa-bbbb-cccc-222222222222/v2.0",
  "subject": "cccccccc-1111-2222-3333-444444444444",
  "audiences": ["api://AzureADTokenExchange"]
}
```

The four fields are worth unpacking. The `name` is a free-form label that lets you reference the FIC later for updates or deletion. The `issuer` is the OpenID Connect issuer URL of the identity provider whose tokens the Blueprint will trust as `client_assertion`s. Here it is the v2 issuer for your tenant. The `subject` is the value Entra will require to see in the `sub` claim of the incoming assertion, and here it is the UAMI's `principalId`. The `audiences` field is the value Entra will require to see in the assertion's `aud` claim, and `api://AzureADTokenExchange` is the canonical audience that Entra-issued MI assertions carry (per the [Entra workload identity federation overview](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation)).

The FIC is what allows the Entra SDK to perform the client_credentials grant against Entra without a client secret. The Entra SDK fetches an assertion from the ACA managed identity endpoint (the assertion has `sub=cccccccc-...`, `aud=api://AzureADTokenExchange`), presents that assertion as `client_assertion` in the client_credentials grant against the Blueprint, and receives an Agent Identity JWT in return.

---

## 4.1 Not running containers in Azure? Use Google Cloud Run (GCP) to run them and add them as the FIC anchor

The deployment walked above runs the Weather Agent on Azure Container Apps. The same Agent Identity model also works when the agent runs outside Azure entirely. The closest non-Azure equivalent of ACA is Cloud Run, the fully-managed serverless-container service on GCP. This section walks what changes and what stays the same when the host swaps from ACA to Cloud Run.

The short answer: the **Entra Agent Identity** stays. The **Blueprint** stays. The **GCP-side bootstrap** (pool, provider, SA, IAM binding) stays. What changes is the **FIC anchor on the Blueprint** and the **sidecar configuration** that tells the Entra SDK how to fetch the `client_assertion`.

### What stays the same

Everything that travels on the wire from the Entra SDK onward is identical to the ACA case. The Entra SDK still performs a `client_credentials` grant against the Blueprint and receives an Agent Identity JWT. The JWT still has `iss=https://login.microsoftonline.com/<tenant>/v2.0`, `aud=<Blueprint appId>`, `sub=<Agent Identity OID>`. GCP STS still validates that JWT through the same provider, against the same audience allow-list, against the same CEL `attributeCondition`. Vertex AI still receives the same `ya29.*` bearer.

So the whole right-hand side of Figure 1 (GCP) is unchanged. The diagrams in sections 2, 3, 5, and 6 hold verbatim.

### What changes: the FIC trio on the Blueprint

On ACA, the Blueprint trusts the UAMI as the source of the `client_assertion`. On Cloud Run, the Blueprint trusts the GCP service account attached to the Cloud Run revision. Side by side:

| FIC field | ACA case (UAMI) | Cloud Run case (GCP SA) |
|---|---|---|
| `issuer` | `https://login.microsoftonline.com/<tenant>/v2.0` | `https://accounts.google.com` |
| `subject` | UAMI `principalId` (GUID) | GCP SA `uniqueId` (21-digit number) |
| `audiences` | `["api://AzureADTokenExchange"]` | `["api://AzureADTokenExchange"]` |

Two of the three fields change. The audience stays the same because that is the canonical value Entra requires for any federated-credential exchange, regardless of which IdP signed the assertion.

The trust shape is identical to the ACA FIC. The only difference is which outside IdP is being trusted as the source of the `client_assertion`: Entra v2 (for an Azure-issued MI assertion) on ACA, or Google (for a GCP-issued OIDC ID token) on Cloud Run.

### What changes: how the sidecar fetches the assertion

Both clouds expose a metadata server that the running workload can call to get a freshly-signed OIDC JWT identifying the attached identity. On Azure that endpoint is the MI endpoint (`IDENTITY_ENDPOINT` + `X-IDENTITY-HEADER` on ACA, or IMDS `169.254.169.254` on VMs). On GCP that endpoint is the metadata server at `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=<aud>`, with the required `Metadata-Flavor: Google` header. Both endpoints are platform-managed, both return a short-lived signed JWT, and both require no key material on disk.

The asymmetry is not on the cloud side. It is on the **SDK** side. Microsoft.Identity.Web (the library inside the Entra SDK sidecar) ships with a built-in caller for the Azure MI endpoint, exposed as the `SignedAssertionFromManagedIdentity` client-credential source type. It does **not** ship with a built-in caller for the GCP metadata server. So:

- On ACA, the sidecar config is one line: `SourceType=SignedAssertionFromManagedIdentity` plus the UAMI's `clientId`. The SDK calls the MI endpoint itself, on demand, every time it needs an assertion.
- On Cloud Run, something outside the SDK has to call the GCP metadata server and hand the result to the SDK. The generic mechanism the SDK exposes for that is `SignedAssertionFromFilePath`. The SDK reads the assertion from the configured file every time it needs one. A small refresher process (an init container, a sidecar loop, or a custom assertion-provider delegate registered in code) curls the GCP metadata server on a refresh cadence and writes the result to that file.

A minimal Cloud Run refresher loop, for illustration:

```bash
# runs in a sidecar container, refreshes every ~40 minutes
# (Google-signed ID tokens last 1 hour)
while true; do
  curl -s -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=api://AzureADTokenExchange" \
    > /var/run/secrets/entra/assertion
  sleep 2400
done
```

The Entra SDK sidecar mounts the same shared volume and is configured with `SourceType=SignedAssertionFromFilePath` pointing at `/var/run/secrets/entra/assertion`.

Both deployments stay secretless. On ACA the "secret" is Azure's ability to mint an MI assertion on demand, gated by the per-replica `IDENTITY_HEADER` nonce. On Cloud Run the "secret" is GCP's ability to mint a metadata-server ID token on demand, gated by the metadata server only being reachable from inside the Cloud Run revision. Neither stores anything long-lived. The file on Cloud Run holds a JWT that expires in an hour and gets overwritten.

### Side-by-side summary

```
                  ACA + UAMI                Cloud Run + GCP SA
                  ──────────                ──────────────────
Host             Azure Container Apps       GCP Cloud Run
Runtime identity UAMI                       GCP service account
Metadata endpt   IDENTITY_ENDPOINT          metadata.google.internal
                 (X-IDENTITY-HEADER nonce)  (Metadata-Flavor: Google)
Assertion iss    Entra v2                   https://accounts.google.com
Assertion sub    UAMI principalId           GCP SA uniqueId
FIC on Blueprint subject = UAMI principalId subject = GCP SA uniqueId
Sidecar source   SignedAssertion-           SignedAssertion-
                   FromManagedIdentity        FromFilePath
                                            (+ refresher loop)
GCP-side WIF     unchanged                  unchanged
Agent Identity   unchanged                  unchanged
On-wire JWT      unchanged                  unchanged
```

The takeaway: the Microsoft Entra Agent ID model is host-agnostic. Wherever the agent runs, you anchor one FIC on the Blueprint at whichever OIDC issuer that host already gives you for free. Azure gives you Entra v2 through the MI endpoint. GCP gives you Google through the metadata server. AWS gives you `sts.amazonaws.com` through IRSA or EKS pod identity. The wire shape downstream is the same in every case.

---

## 5. GCP-side bootstrap

The GCP-side bootstrap is one pool, one provider, one service account, and one IAM binding. Everything below uses the [`gcloud iam`](https://cloud.google.com/sdk/gcloud/reference/iam) command surface. Project id `acme-agent-prod-001` and project number `100000000001` are synthetic stand-ins for the real project values. The project id is what you pass on the gcloud command line. The project number is what appears inside the `principal://` URI of the IAM binding because that URI is built from immutable identifiers.

| # | Command | What it creates | How to verify |
|---|---|---|---|
| 1 | `gcloud iam workload-identity-pools create entra-prod-pool --location=global --project=acme-agent-prod-001` | The pool (one-time per environment, [docs](https://cloud.google.com/iam/docs/manage-workload-identity-pools-providers#create_pool)) | `gcloud iam workload-identity-pools list --location=global` |
| 2 | `gcloud iam workload-identity-pools providers create-oidc entra-agentid-provider ...` (full command below) | The Agent Identity provider, [docs](https://cloud.google.com/sdk/gcloud/reference/iam/workload-identity-pools/providers/create-oidc) | `gcloud iam workload-identity-pools providers describe entra-agentid-provider ...` |
| 3 | `gcloud iam service-accounts create weather-agent-agentid --project=acme-agent-prod-001` | The agent's GCP service account | `gcloud iam service-accounts describe weather-agent-agentid@acme-agent-prod-001.iam.gserviceaccount.com` |
| 4 | `gcloud projects add-iam-policy-binding acme-agent-prod-001 --member=serviceAccount:weather-agent-agentid@... --role=roles/aiplatform.user` | Grants Vertex AI access to the SA | `gcloud projects get-iam-policy acme-agent-prod-001` |
| 5 | `gcloud iam service-accounts add-iam-policy-binding weather-agent-agentid@... --role=roles/iam.workloadIdentityUser --member=principal://.../subject/aaaaaaaa-...` | Lets the Agent Identity subject impersonate the SA | `gcloud iam service-accounts get-iam-policy weather-agent-agentid@...` |

The `create-oidc` command is the most interesting, and it is the place where the audience-pun and the CEL condition show up explicitly.

```bash
gcloud iam workload-identity-pools providers create-oidc entra-agentid-provider \
  --location=global \
  --workload-identity-pool=entra-prod-pool \
  --project=acme-agent-prod-001 \
  --issuer-uri="https://login.microsoftonline.com/11111111-aaaa-bbbb-cccc-222222222222/v2.0" \
  --allowed-audiences="bbbbbbbb-1111-2222-3333-444444444444,api://bbbbbbbb-1111-2222-3333-444444444444" \
  --attribute-mapping="google.subject=assertion.sub" \
  --attribute-condition="assertion.sub == 'aaaaaaaa-1111-2222-3333-444444444444'"
```

Each flag is doing real work, and it is worth pulling apart what each one says.

- `--issuer-uri` is the OpenID Connect issuer URL of the IdP whose tokens this provider will trust. GCP STS fetches `<issuer-uri>/.well-known/openid-configuration` and `<jwks_uri>` from there to validate the JWT signature. The URL has to be exactly the **v2** Entra issuer (`/v2.0` suffix), because v1 issuer URIs (`https://sts.windows.net/<tenant>/`) point at a different metadata endpoint that does not match what `/oauth2/v2.0/token` emits in the JWT `iss` claim.
- `--allowed-audiences` is the byte-for-byte allow-list of values that may appear in the JWT `aud` claim. Both forms (URI and bare GUID) are listed because Entra normalizes URIs to GUIDs on the wire, as covered in the WIF-contract section above. The audience here is the Blueprint's identifier URI, and the section below on what goes on the wire explains why that is the right choice.
- `--attribute-mapping` is the CEL expression list that copies claims out of the incoming assertion and into Google-side attributes that the principal URI is built from. `google.subject=assertion.sub` is the minimum mapping. It says "use the `sub` claim verbatim as the federated principal's subject." More elaborate mappings can copy groups, custom claims, or apply transforms, but this article does not need them.
- `--attribute-condition` is a CEL boolean expression that must evaluate to `true` for the token to be accepted. This is the per-workload narrowing that pins on the exact subject the provider trusts. Without an attribute condition, any Entra-issued token from any caller in the tenant with the right audience would be accepted, which is too broad for production.

A small but important detail: the role granted to the service account at project scope (`roles/aiplatform.user`) does **not** transfer between SAs. If you later add a second Agent Identity with its own SA, that second SA needs its own project-scoped role grant. If you ever forget, the federation will succeed, the impersonation will succeed, and the Vertex AI call will fail with a `403 PERMISSION_DENIED` whose body names the missing `aiplatform.endpoints.predict` permission. The failure-modes section below covers that case.

**Figure 5. GCP-side object graph after bootstrap: one pool, one provider, one service account, two role grants.**

```
                      (1) acme-agent-prod-001
                       (project number 100000000001)
                                    │
                                    ▼
                       (2) entra-prod-pool
                                    │
                                    ▼
                       (3) entra-agentid-provider
                           sub == aaaaaaaa-…
                                    │
                                    ▼
                       (4) weather-agent-agentid
                                    │
                          ├── roles/aiplatform.user (project)
                          │
                          └── roles/iam.workloadIdentityUser
                                (on this SA, for principal
                                 with subject = aaaaaaaa-…)
```

---

## 6. What goes on the wire

The interesting moment in any federation flow is the first POST to the outside cloud's STS, because that is the request that carries the identity claim. For WIF the request goes to `https://sts.googleapis.com/v1/token` and carries six form fields, all defined in [RFC 8693, OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693). The fields are summarized below.

| Field | Value | What it does |
|---|---|---|
| `grant_type` | `urn:ietf:params:oauth:grant-type:token-exchange` | Selects the token-exchange grant, distinct from the more common `client_credentials` or `authorization_code` grants. |
| `audience` | `//iam.googleapis.com/projects/100000000001/locations/global/workloadIdentityPools/entra-prod-pool/providers/entra-agentid-provider` | Names the provider the assertion is being presented to. This is the WIF resource URI built from project number, pool name, and provider name. |
| `scope` | `https://www.googleapis.com/auth/cloud-platform` | Scopes the resulting federated token. The cloud-platform scope is the broadest one and is what subsequent `iamcredentials:generateAccessToken` expects. |
| `requested_token_type` | `urn:ietf:params:oauth:token-type:access_token` | Asks STS for an access token (as opposed to a JWT or a refresh token). |
| `subject_token_type` | `urn:ietf:params:oauth:token-type:jwt` | Tells STS the input is a JWT. STS knows to validate the signature, claims, and audience. |
| `subject_token` | (the Agent Identity JWT) | The actual identity assertion. |

The `subject_token` field carries the Agent Identity JWT, which is RS256-signed by `https://login.microsoftonline.com/11111111-aaaa-bbbb-cccc-222222222222/v2.0` and has `idtyp=app` with `appidacr=2`. A decoded view of the claims that matter is below.

**Figure 6. The Agent Identity subject_token shape, with the claims that drive WIF validation called out.**

```
       Agent Identity subject_token
       ────────────────────────────
       fetched via Entra SDK
       /AuthorizationHeaderUnauth-
       enticated/gcp-sts?Agent…

       {
         "iss": "…11111111…/v2.0",
         "aud": "bbbbbbbb-…"
                ← Blueprint GUID
                  (URI form normalized
                   to GUID on the wire)
         "sub": "aaaaaaaa-…"  ← Agent Identity OID
         "oid": "aaaaaaaa-…"
         "appid": "aaaaaaaa-…"
         "xms_par_app_azp": "bbbbbbbb-…"  ← Blueprint
                  (v1 tokens only; omitted on v2.
                   GCP WIF ignores this claim either way —
                   it pins on `sub` via the CEL condition.)
         "app_displayname": "Weather Agent (GCP)"
         "idtyp": "app",
         "appidacr": "2"
       }
```

Each of the claims that matter for WIF is worth a one-line gloss.

- `iss` is the OpenID Connect issuer URL of the token. WIF validates the JWT signature against the JWKS published at `<iss>/.well-known/openid-configuration`. The provider's `issuerUri` has to be exactly equal to this value. The deployment uses the v2 issuer for your tenant.
- `aud` is the audience the token was minted for. The audience pun (Entra normalizes URIs to GUIDs) means the wire value differs from what the Entra SDK asked for. The Entra SDK asks Entra for `api://bbbbbbbb-.../.default` and receives a token with `aud=bbbbbbbb-...`. The provider audience allow-list includes both forms to be safe.
- `sub` is the subject. For the Agent Identity JWT, `sub` equals the Agent Identity's `oid`. This is the value the provider's `attributeCondition` pins on.
- `oid` is the Entra object id, which for app-only tokens equals `sub`. It is duplicated because some downstream APIs use `oid` rather than `sub` to identify the principal.
- `appid` is the application id (client id) of the application the token was issued for. The Agent Identity is a service principal hosted by the Blueprint, and the token records the Agent Identity as `oid`/`appid` and the Blueprint as `xms_par_app_azp`.
- `xms_par_app_azp` is the **parent application** claim. It records the appId of the Blueprint that minted this Agent Identity JWT. Downstream Entra-aware APIs (the protected resource on the second leg of this article, such as the Weather API in this repo) use this claim to prove that the token was minted under a Blueprint they trust. **This claim is emitted on v1 tokens only.** On v2 tokens (the form GCP WIF requires, and the form a production Weather API with `requestedAccessTokenVersion=2` will see) the claim is omitted, and the Blueprint-provenance check shifts to the `aud`+`roles` pair as described in *From sample to production* in §7. GCP WIF itself does not consult this claim under either token version. It pins on `sub` via the CEL condition.
- `app_displayname` is the human-readable name of the application that requested the token. Useful for log readability and absolutely nothing else.
- `idtyp` is the identity type. `app` means the token is app-only (no user behind it). The alternative is `user` for delegated tokens. This deployment emits `app` tokens.
- `appidacr` is the application authentication context reference. `2` means the client authenticated with a confidential credential (certificate, secret, or signed assertion), as opposed to `0` (public client) or `1` (client password). The deployment shows `2` because the underlying client_credentials grant is using a SignedAssertionFromManagedIdentity, which is a confidential credential.

There is one more wire-level detail worth knowing about, and it is the **Entra v1 vs v2 issuer distinction**. There are two issuer URLs in use across the Microsoft identity platform. The v1 issuer (`https://sts.windows.net/<tenant>/`) is what the WS-Fed / OAuth 1.0 era of tokens carries. The v2 issuer (`https://login.microsoftonline.com/<tenant>/v2.0`) is what the OIDC `/oauth2/v2.0/token` endpoint emits, and is what the GCP WIF provider has to be configured for. Configuring the WIF provider with the v1 issuer URI is one of the most common first-deployment mistakes, and the symptom is a generic STS error with no clear pointer to which gate failed.

### The Agent Identity audience is the Blueprint itself

The audience on the Agent Identity JWT is the Blueprint's own identifier URI. The Agent Identity is asking Entra for a token whose `aud` is **the very app registration that hosts it**, and that token is then presented to GCP STS, which is not the audience named on the token.

Two things make this look wrong on first read, even though it is correct. First, `aud` and `xms_par_app_azp` both carry the same Blueprint GUID, so from Entra's point of view the app is talking to itself. Second, the audience is not the recipient. GCP STS never validates `aud` cryptographically the way Entra does. It just compares the string against its `allowedAudiences` list. Audience and recipient are two different things in this leg.

#### Why GCP cares which audience you pick

GCP's federation contract has exactly two narrowing gates. The first is `allowedAudiences` on the provider. The second is the CEL `attributeCondition`, which typically pins on `sub`. The audience is the gate that decides **which population of Entra-minted tokens is even eligible to reach the CEL evaluator**. If the audience is loose, the CEL gate is doing all the work alone. If the audience is tight, the two gates fail independently for different reasons, and a misconfiguration in one cannot be papered over by the other.

A concrete failure mode makes this obvious. Suppose you listed `https://graph.microsoft.com` in `allowedAudiences`. **Any** Entra app in your tenant that can mint a Graph-audience token could then present its JWT as a `subject_token` and clear the audience check. Your CEL `attributeCondition` on `sub` would still need to match, but you would be relying on a single pinning gate instead of two. By using the Blueprint's own identifier URI as the audience, you guarantee that only tokens minted **by and for** this Blueprint can satisfy the audience check at all. The CEL condition then narrows further to a specific Agent Identity OID. Two independent gates, each owned by a different identity object.

#### When one Blueprint with many Agent Identities is the right shape

Use a single Blueprint whenever the Agent Identities share a **trust boundary** in the threat-modeling sense. Same codebase, same runtime, same operator, same blast radius if any one of them is compromised. The Microsoft Entra Agent ID [design-patterns guidance](https://learn.microsoft.com/en-us/entra/agent-id/concept-agent-id-design-patterns) calls this the **domain worker** pattern. One Blueprint, one Agent Identity per agent role, baseline permissions inherited from the Blueprint, role-specific permissions on each Agent Identity.

For GCP federation that translates cleanly. The audience is `api://<Blueprint>`. The provider's `allowedAudiences` lists exactly that one Blueprint URI. The CEL `attributeCondition` pins on the specific Agent Identity OID for whichever GCP service account it is allowed to impersonate. Adding another Agent Identity under that same Blueprint costs you one extra CEL clause (or one extra service-account binding) and nothing else, because the Blueprint did not change.

#### When you actually need separate Blueprints

Use separate Blueprints when the Agent Identities sit on **different sides of a trust boundary**. Different teams, different runtimes, different secrets, different blast radii. The design-patterns guidance calls this the **concurrent orchestrator** pattern and is explicit on the reason. Agents that cross trust boundaries require separate Blueprints because the Blueprint's credentials are scoped to its trust domain, and a compromise in one domain must not affect peer agents.

The GCP-side reason is a direct consequence of that rule. Entra mints `aud=<Blueprint>` for every Agent Identity hosted by that Blueprint, so as long as two Agent Identities live under the same Blueprint, **their tokens are indistinguishable at the audience layer**. The CEL `attributeCondition` becomes the only per-identity narrowing gate at GCP STS. For same-trust-boundary co-tenants that is acceptable. An exact-match `assertion.sub == "<oid>"` per identity is enough. For cross-trust-boundary co-tenants it is dangerous. A loose CEL condition (a prefix match, a regex, a `startsWith`, or no condition at all) lets any Agent Identity under the Blueprint present its token as `subject_token` and clear the audience check on behalf of any other Agent Identity under the same Blueprint.

Putting cross-trust-boundary Agent Identities into separate Blueprints restores audience as an independent pinning gate (`api://<Blueprint-A>` vs `api://<Blueprint-B>`), and the CEL condition becomes reinforcement rather than the sole barrier.

#### The hardening rule

Co-locate Agent Identities under one Blueprint when they share a trust boundary. Give them separate Blueprints when they cross one. Let the CEL `attributeCondition` always pin on an exact-match OID. Trust boundary is a threat-modeling decision the application makes. Microsoft Entra Agent ID does not infer it for you.

The same logic applies to the Entra SDK config. Each DownstreamApi entry corresponds to exactly one audience, because the audience is what the downstream pins on. The `graph-app` entry in this article's sample carries the protected-resource scope `api://weather-api-prod/.default` (the entry's name is a historical artifact, documented in *From sample to production* in §7). The `gcp-sts` entry carries `api://<Blueprint>/.default`. A future `aws-sts` entry would carry its own audience. The Agent Identity JWTs minted for the GCP leg and the protected-resource leg are therefore two distinct tokens with different `aud` claims, even though every other claim (`iss`, `sub`, `oid`, `appid`, `xms_par_app_azp`) is identical. They are fetched from different Entra SDK endpoints and cannot be substituted for each other.

---

## 7. End-to-end walkthrough

The agent calls Vertex AI as the Entra Agent Identity, and the Entra SDK is on the GCP hot path as well as the protected-resource hot path. The Entra SDK has a DownstreamApi named `gcp-sts` whose scope is `api://bbbbbbbb-.../.default` and whose `BaseUrl` is `https://sts.googleapis.com/`. A custom `_AgentIdSidecarSupplier` inside the agent's `google-auth` glue calls `GET /AuthorizationHeaderUnauthenticated/gcp-sts?AgentIdentity=aaaaaaaa-...` each time `google-auth` asks for a new subject token, and the Entra SDK returns an Agent Identity JWT whose audience is the Blueprint.

The supplier is a small Python class that conforms to `google-auth`'s [`SubjectTokenSupplier`](https://google-auth.readthedocs.io/en/master/reference/google.auth.aio.credentials.html) protocol. It is registered as the `subject_token_supplier` on an `identity_pool.Credentials` instance, and `google-auth` calls its `get_subject_token(...)` method whenever a new subject token is needed. The agent process never imports MSAL and never touches the Entra endpoint. The Entra SDK does all of that work.

The protected-resource leg shows the same Agent Identity being reused for an unrelated downstream call inside the same agent turn. The Entra SDK mints a second Agent Identity JWT, this time with `aud=api://weather-api-prod` instead of the Blueprint, and the protected resource validates it with the seven-check token validation routine described below.

### Configuration recap

The identifiers below are synthetic stand-ins for the real deployment values. The shape of each field is what matters.

| Object | Value |
|---|---|
| Container app FQDN | `agent-app-agentid.livefern-1a2b3c4d.eastus2.azurecontainerapps.io` |
| UAMI (FIC anchor only) | `mi-weather-agent-agentid` (`principalId=cccccccc-1111-2222-3333-444444444444`) |
| Blueprint | `bbbbbbbb-1111-2222-3333-444444444444` (`"GCP ACA Blueprint"`) |
| Agent Identity | `aaaaaaaa-1111-2222-3333-444444444444` (`"Weather Agent (GCP ACA)"`) |
| Sidecar GCP DownstreamApi (Entra SDK config) | `gcp-sts` (scope `api://bbbbbbbb-.../.default`, `BaseUrl=https://sts.googleapis.com/`, `RequestAppToken=true`) |
| Toggle env var | `USE_AGENT_ID_FOR_GCP=true`, `SIDECAR_GCP_DOWNSTREAM=gcp-sts` |
| GCP provider | `entra-agentid-provider` (`sub == "aaaaaaaa-..."`) |
| GCP service account | `weather-agent-agentid@acme-agent-prod-001.iam.gserviceaccount.com` |
| GCP role | `roles/aiplatform.user` at project scope |

**Figure 7. End-to-end sequence: Agent Identity federates to GCP (via the Entra SDK), and the same Entra SDK mints a second Agent Identity JWT (with a different audience) for the protected-resource call inside the same agent turn.**

The Entra SDK has one job in both bands: **mint Agent Identity JWTs** (via `client_credentials` + FMI Path). What differs between the bands is **what the agent does with that JWT downstream**. On the GCP leg the agent federates the JWT into GCP. On the protected-resource leg the agent presents it as `Bearer` to a protected resource the customer owns.

**Token legend** (color = which JWT is on the wire):
![MI JWT](https://img.shields.io/badge/%E2%AC%A2-MI%20JWT-ea580c?style=flat-square) UAMI managed-identity JWT, `aud=api://AzureADTokenExchange` &nbsp;&nbsp; ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square) Agent Identity JWT, Blueprint audience &nbsp;&nbsp; ![Agent ID JWT (API)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28API%29-CC79A7?style=flat-square) Agent Identity JWT, protected-resource audience

**Exchange legend** (marks the row that performs the federation handshake):
![FIC](https://img.shields.io/badge/-FIC-f59e0b?style=flat-square) Microsoft Entra Federated Identity Credential exchange (mints an AI JWT) &nbsp;&nbsp; ![WIF](https://img.shields.io/badge/-WIF-0d9488?style=flat-square) GCP Workload Identity Federation exchange (consumes an AI JWT, returns a `ya29.*`)

**Band legend:** 🟦 Entra + GCP path &nbsp;&nbsp; 🟪 Entra + Protected Resource path


<img width="7152" height="5592" alt="image" src="https://github.com/user-attachments/assets/f2e0de9c-293e-47ed-9808-4b079333986b" />


> 🖼️ **Prefer an image?** View [Figure 7 as a high-resolution PNG](https://github.com/razi-rais/3P-Agent-ID-Demo/blob/main/sidecar/llm-agent-google/docs/images/agent-id-outbound-federation-gcp-figure7.png) (7152 × 5592, ~675 KB) for print, slides, or anywhere Mermaid does not render.

> **Diagram visual encoding** matches the badges above the table:
> **Outer band** = which leg of the agent turn (🟦 GCP, 🟪 Protected Resource). **Nested rect** = which exchange is happening (🟧 MI fetch, 🟡 FIC, 🟢 WIF). **Emoji on an arrow** = which JWT is on the wire (🟧 MI JWT, 🔵 Agent ID JWT for GCP STS, 🟣 Agent ID JWT for the API). The FIC→WIF chain reads visually as the 🔵 token born inside the amber FIC rect being consumed by the teal WIF rect three arrows later.
>
> **Palette is colorblind-safe** (Wong-inspired). Token color mirrors the outer band: 🔵 GCP-bound tokens match the 🟦 GCP band, 🟣 API-bound tokens match the 🟪 Protected Resource band. Shape carries information too: circles flow on arrows (tokens), squares fill zones (bands and exchanges). No red anywhere, and the only same-family pair (orange MI · amber FIC) is positionally disambiguated by the adjacent nested rects.
>
> **A note on the FIC label.** `client_credentials` is an OAuth 2.0 grant where the client proves its identity with a credential. The credential is normally a shared secret. With a **Federated Identity Credential** the credential is a **signed JWT assertion** (`client_assertion`) instead. In this article that assertion is the UAMI's MI JWT (`aud=api://AzureADTokenExchange`), and the FMI Path header on the request selects which Agent Identity under the Blueprint to mint a token for. The Blueprint trusts the UAMI as a federated credential source. The assertion proves the request came from that UAMI.

### Step-by-step walkthrough

The prompt the client sends is `{"message":"weather in London","llm_mode":"vertex"}`. The `llm_mode=vertex` flag is what enables the Vertex AI path. The default `llm_mode` is `direct`, which bypasses Gemini entirely and extracts the city heuristically from the prompt text. The verification-commands section below covers the `curl` command and explains why this matters for reproducing the walkthrough.

Each row below maps one-to-one to the autonumbered arrow in Figure 7. The first column is the Mermaid step number. The **Band** column carries the band swimlane (🟦 or 🟪) and, where relevant, the federation-exchange tag (![FIC](https://img.shields.io/badge/-FIC-f59e0b?style=flat-square) or ![WIF](https://img.shields.io/badge/-WIF-0d9488?style=flat-square)) marking the row where that handshake completes. JWT badges in the **What happens** column are color-coded by which JWT is on the wire: ![MI JWT](https://img.shields.io/badge/%E2%AC%A2-MI%20JWT-ea580c?style=flat-square), ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square), or ![Agent ID JWT (API)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28API%29-CC79A7?style=flat-square). Reuse of the same color across rows means the same token is being passed forward. A color change means a new token has just been minted. The FIC→WIF chain is the entire point of this article: step 5 is the FIC, step 8 is the WIF, and the ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square) produced by the former is consumed by the latter.

| Step | Band | What happens | Verified evidence |
|---|---|---|---|
| 1 | (none) | Client posts `POST /api/chat` to the agent app | HTTP 200 from `agent-app-agentid.livefern-1a2b3c4d.eastus2.azurecontainerapps.io` |
| 2 | 🟦 | Agent's custom `_AgentIdSidecarSupplier` calls the Entra SDK `/gcp-sts` endpoint for the WIF subject_token | `GET http://localhost:5000/AuthorizationHeaderUnauthenticated/gcp-sts?AgentIdentity=aaaaaaaa-...` |
| 3 | 🟦 | Entra SDK calls the ACA managed identity endpoint for the inner MI assertion | `GET http://localhost:12356/msi/token?resource=api://AzureADTokenExchange` with `X-IDENTITY-HEADER`. Entra SDK log: Token Acquisition `scope api://AzureADTokenExchange, source IdentityProvider`. |
| 4 | 🟦 | ![MI JWT](https://img.shields.io/badge/%E2%AC%A2-MI%20JWT-ea580c?style=flat-square) MI endpoint returns the UAMI assertion | JWT decode: `sub=oid=cccccccc-...`, `aud=api://AzureADTokenExchange` (GUID `fb60f99c-...-936` on the wire), `iss=https://login.microsoftonline.com/<tenant>/v2.0` |
| 5 | 🟦 ![FIC](https://img.shields.io/badge/-FIC-f59e0b?style=flat-square) | Entra SDK performs client_credentials against Entra with the UAMI assertion as `client_assertion` and the Blueprint scope | Two more Token Acquisitions: `api://AzureADTokenExchange/.default` (MSAL cache form) and `api://bbbbbbbb-.../.default` with FMI Path = `aaaaaaaa-...`. |
| 6 | 🟦 | ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square) Entra returns the Agent Identity JWT to the Entra SDK | JWT decode: `sub=oid=appid=aaaaaaaa-...`, `aud=api://bbbbbbbb-...` (GUID `bbbbbbbb-...` on the wire), `xms_par_app_azp=bbbbbbbb-...`, `app_displayname="Weather Agent (GCP ACA)"`. |
| 7 | 🟦 | ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square) Entra SDK returns the JWT to the agent in an `Authorization: Bearer` header | Agent log line: `[2.C TOKEN RECEIVED] Got Agent Identity token (TR) from sidecar`. |
| 8 | 🟦 ![WIF](https://img.shields.io/badge/-WIF-0d9488?style=flat-square) | ![Agent ID JWT (GCP STS)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28GCP%20STS%29-0072B2?style=flat-square) Agent POSTs the JWT as `subject_token` to `sts.googleapis.com/v1/token` | Provider `entra-agentid-provider` matches the CEL condition `assertion.sub == "aaaaaaaa-..."`. |
| 9 | 🟦 | GCP STS returns a federated access token | STS host: `sts.googleapis.com`. |
| 10 | 🟦 | Agent calls `iamcredentials:generateAccessToken` to impersonate the SA | URL: `iamcredentials.googleapis.com/.../weather-agent-agentid@acme-agent-prod-001.iam.gserviceaccount.com:generateAccessToken`. |
| 11 | 🟦 | iamcredentials returns the `ya29.*` access token | SA unique id: `100000000000000000001`. |
| 12 | 🟦 | Agent calls Vertex AI Gemini (turn 1) | Agent log line: `[GCP] Processing query with Vertex AI LLM`. POST to `us-central1-aiplatform.googleapis.com/.../gemini-2.5-flash-lite:generateContent`. |
| 13 | 🟦 | Gemini returns a tool_call | Agent log line: `[1.A LLM DECISION (turn 1)] Gemini chose to call tool(s): get_weather` (input 160 tokens, output 5 tokens). |
| 14 | 🟪 | Agent calls the Entra SDK `/graph-app` endpoint for an Agent Identity token (protected-resource audience) | `GET http://localhost:5000/AuthorizationHeaderUnauthenticated/graph-app?AgentIdentity=aaaaaaaa-1111-2222-3333-444444444444`. The endpoint is named `/graph-app` for historical reasons in the reference sample. The audience it returns is configured in the Entra SDK's `DownstreamApi` entry, documented in *From sample to production* below. |
| 15 | 🟪 | Entra SDK calls the ACA managed identity endpoint for the inner MI assertion (typically a cache hit on this second leg) | Entra SDK log on first call of the lifetime: Token Acquisition `scope api://AzureADTokenExchange, source IdentityProvider`. On subsequent calls: source `Cache`. |
| 16 | 🟪 | ![MI JWT](https://img.shields.io/badge/%E2%AC%A2-MI%20JWT-ea580c?style=flat-square) MI endpoint returns the UAMI assertion | Same shape as step 4 (`sub=cccccccc-...`, `aud=api://AzureADTokenExchange`). |
| 17 | 🟪 ![FIC](https://img.shields.io/badge/-FIC-f59e0b?style=flat-square) | Entra SDK performs client_credentials against Entra with the UAMI assertion as `client_assertion`, FMI Path = `aaaaaaaa-...`, scope = `api://weather-api-prod/.default` | Two more Token Acquisitions: `api://AzureADTokenExchange/.default` (MSAL cache form) and `api://weather-api-prod/.default` with FMI Path = `aaaaaaaa-...`. |
| 18 | 🟪 | ![Agent ID JWT (API)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28API%29-CC79A7?style=flat-square) Entra returns the Agent Identity JWT to the Entra SDK (Protected-Resource audience) | Decoded claims: `aud=api://weather-api-prod` (GUID `<weather-api-appId>` on the wire, the same URI→GUID normalization described in §6), `appid=oid=sub=aaaaaaaa-...`, `xms_par_app_azp=bbbbbbbb-...` (v1 tokens only, see step 21 note), `roles=["Weather.Read.All"]`. Same Agent Identity subject as step 6, different audience and role set. |
| 19 | 🟪 | ![Agent ID JWT (API)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28API%29-CC79A7?style=flat-square) Entra SDK returns the JWT to the agent in an `Authorization: Bearer` header | Agent log line: `[2.C TOKEN RECEIVED] Got Agent Identity token (TR) from sidecar`. |
| 20 | 🟪 | ![Agent ID JWT (API)](https://img.shields.io/badge/%E2%AC%A2-Agent%20ID%20JWT%20%28API%29-CC79A7?style=flat-square) Agent calls the protected resource with the Agent Identity bearer | `GET https://weather-api-agentid.internal.livefern-1a2b3c4d.eastus2.azurecontainerapps.io/weather?city=London` (the `.internal` segment marks this as a private FQDN inside the ACA environment, not reachable from outside). |
| 21 | 🟪 | Protected resource runs the seven-check validation and returns weather | All seven checks pass: signature RS256 via JWKS, issuer `https://login.microsoftonline.com/<tenant>/v2.0` (the Weather API has `requestedAccessTokenVersion=2` so it accepts the same v2 issuer the GCP provider trusts, see callout below), expiry, `xms_par_app_azp=bbbbbbbb-...` (v1-only claim, absent when the resource is v2, in which case the `aud=api://weather-api-prod` + `roles=["Weather.Read.All"]` pair is the production-grade Blueprint-provenance check, because the role only appears on tokens minted under a Blueprint that was admin-consented for it), flow type `Autonomous Agent`, app id `aaaaaaaa-...`, audience `api://weather-api-prod`. Response: `London / 60F / Light Drizzle`, `is_agent_identity=True`, `validated_by=Agent Identity Token`. |
| 22 | 🟦 | Agent calls Vertex AI Gemini (turn 2, with cached `ya29.*`) | `_GCP_CREDS` cache hit in the agent process. The Entra SDK is not called again on the GCP leg until a new subject token is needed. |
| 23 | 🟦 | Gemini composes the final natural-language answer | Agent log line: `[4.A LLM ANSWER (turn 2)]` (input 301 tokens, output 35 tokens). |
| 24 | (none) | Agent returns the response to the client | `total_tokens=501`, HTTP 200. |

#### From sample to production: registering a real protected-resource app

The walkthrough above uses `api://weather-api-prod` as the audience and `Weather.Read.All` as the role. This is the **production-correct** rendering. There is nothing Graph-specific about the Microsoft Entra Agent ID pattern, and in real deployments the audience should be a downstream app you own, not Microsoft Graph. To run end to end against your own protected resource:

1. **Register the protected-resource app** in Entra: `az ad app create --display-name "Weather API" --identifier-uris "api://weather-api-prod"`.
2. **Expose an app role** on the Weather-API app (Portal: *App registrations → Weather API → App roles → Create app role*). Set value `Weather.Read.All`, allowed member type `Applications`.
3. **Grant the role to the Blueprint and admin-consent it**: `az ad app permission add --id bbbbbbbb-... --api <weather-api-appId> --api-permissions <role-id>=Role` then `az ad app permission admin-consent --id bbbbbbbb-...`. After consent, the Blueprint can mint Agent Identity tokens with `aud=api://weather-api-prod` and `roles=["Weather.Read.All"]`.
4. **Wire the Entra SDK**: change the `graph-app` DownstreamApi entry's scope from `https://graph.microsoft.com/.default` to `api://weather-api-prod/.default`. (You can also rename the entry, for example to `weather-api`, in which case the endpoint path the agent calls becomes `/AuthorizationHeaderUnauthenticated/weather-api?AgentIdentity=...`.)
5. **Set `requestedAccessTokenVersion=2` on the protected-resource app manifest, and validate three independent gates in the resource**: signature + issuer (`https://login.microsoftonline.com/<tenant>/v2.0`), audience (GUID form of `api://weather-api-prod`, because Entra normalizes URIs to GUIDs on the wire, see §6), and app role (`Weather.Read.All`). In v2 tokens the Microsoft-private `xms_par_app_azp` provenance claim is **not emitted**. The audience+role pair is the production-grade provenance check, because the role only appears on tokens minted under a Blueprint that was admin-consented for it. Choosing v2 also keeps the issuer URL identical across the GCP leg and the protected-resource leg (the GCP WIF provider requires v2, see §5), which simplifies operational reasoning about both legs.

Everything else in this article (the Blueprint, the Agent Identity, the FMI Path, the seven-check validation) stays identical. **Only the `aud` claim and the `roles` claim change.** That is the whole point: the Agent ID pattern is audience-agnostic.

> The reference sample shipped in this repo currently uses `https://graph.microsoft.com` and `User.Read.All` to avoid requiring a second app registration during initial setup. If you reproduce against the unmodified sample, only the `aud` and `roles` fields in steps 18 and 21 will read `https://graph.microsoft.com` / `User.Read.All` instead of `api://weather-api-prod` / `Weather.Read.All`. Every other claim, log line, and timing characteristic in the table is identical.

A claim about the Entra SDK worth pulling out: the Entra SDK always uses [`SignedAssertionFromManagedIdentity`](https://learn.microsoft.com/en-us/entra/msal/dotnet/acquiring-tokens/web-apps-apis/client-credential-flows#signedassertion) to replace what would otherwise be a client secret in the client_credentials grant. The inner assertion is the UAMI's MI JWT. The Entra SDK fetches it once and reuses it for the lifetime of the assertion, minting Agent Identity tokens for whichever audience the calling DownstreamApi is configured for. In this walkthrough that is `api://bbbbbbbb-...` (the Blueprint) on the GCP leg and `api://weather-api-prod` on the protected-resource leg. The Entra SDK's behavior is identical in both cases. Only the requested audience changes.

---

## 8. The Entra SDK token taxonomy

A piece of terminology confusion comes up repeatedly when teams first deploy the Entra SDK, and it is worth clearing up before reading the failure-modes section. The Entra SDK **only mints OAuth 2.0 access tokens**. The Entra SDK **never** mints OIDC ID tokens, ever, even though the Entra app registration page has a checkbox labeled "ID tokens" that suggests otherwise.

In a normal turn, the Entra SDK performs three Token Acquisitions, and all three are app-only access tokens. The table below summarizes them in concept.

| # | Entra SDK log entry | OAuth/OIDC type | Purpose | Audience |
|---|---|---|---|---|
| 1 | scope `api://<Blueprint>/.default`, FMI Path = `<Agent Identity OID>` | App-only access token | Agent Identity bearer token sent to GCP STS as `subject_token` | Blueprint identifier URI |
| 2 | scope `api://AzureADTokenExchange` | App-only access token, used **as** `client_assertion` (FIC) | Replaces the client secret in the client_credentials grant (SignedAssertionFromManagedIdentity) | `api://AzureADTokenExchange` (Azure AD Token Exchange first-party appId on wire) |
| 3 | scope `api://AzureADTokenExchange/.default` | App-only access token, MSAL cache form | Internal MSAL bookkeeping | `api://AzureADTokenExchange` |

All three are JWT, RS256, with `idtyp=app` and `appidacr=2`. Pure non-human tokens, every one of them.

The reason the Entra SDK can never return an ID token, regardless of any app-registration checkbox, is that the underlying OAuth flow does not support it. The Entra SDK calls `/oauth2/v2.0/token` with `grant_type=client_credentials`. Per the [OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc6749) and [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html) specifications, the client_credentials grant cannot return an `id_token`. There is no `/authorize` hop, no user, no `nonce`, no consent screen. ID tokens only exist in OIDC sign-in flows (authorization_code, implicit, hybrid) that go through `/authorize`. The "ID tokens" and "Access tokens" checkboxes under *Authentication, Implicit grant and hybrid flows* on the app registration page control browser implicit and hybrid flows only. They are irrelevant to the Entra SDK's flow. Both checkboxes can be off and the Entra SDK still works.

When you are reading a JWT and trying to figure out which kind of token it is, the disambiguator is the combination of `aud`, `idtyp`, and `appidacr`.

| Combination | What kind of token it is |
|---|---|
| `aud` equals a client ID | ID token (who signed in) |
| `aud` equals an API's appId or identifier URI, and `idtyp=user` | Delegated access token (user-via-app) |
| `aud` equals an API's appId or identifier URI, and `idtyp=app` with `appidacr=2` | App-only access token. This is what the Entra SDK emits. |

---

## 9. GCP-side failure modes

The provider's CEL condition, the SA's IAM binding, and the SA's project-scoped roles give you four independent ways to misconfigure the same identity, and all four produce similar-looking errors with subtly different signals. The table below is the failure-mode reference you will want next to you the day something is not working.

| # | Failure | What you see | Where to look first | Verify command |
|---|---|---|---|---|
| 1 | `sub` claim does not match provider's CEL condition | `403 The given credential is rejected by the attribute condition` from STS (host: `sts.googleapis.com`) | Compare the JWT's `sub` claim to the provider's `attributeCondition`. They have to be the same GUID. | `gcloud iam workload-identity-pools providers describe <provider> --location=global --workload-identity-pool=<pool>` |
| 2 | `aud` claim is not in the provider's allowed-audiences list | `400 Invalid value for "audience" parameter` or `403 The token audience ... is not allowed` from STS | The provider's `oidc.allowedAudiences` has to include both the URI form (`api://...`) and the bare GUID, because Entra normalizes between them on the wire. | same as above |
| 3 | `iss` claim does not match provider's issuer URI | `400 The token issuer doesn't match the issuer expected by this provider` from STS | The provider's `oidc.issuerUri` has to be exactly `https://login.microsoftonline.com/<tenant-id>/v2.0`. The `sts.windows.net` issuer is **v1**, the `login.microsoftonline.com/.../v2.0` issuer is **v2**. The provider is configured for v2. | same as above |
| 4 | STS accepts the token but `iamcredentials:generateAccessToken` returns 403 | `403 Permission iam.serviceAccounts.getAccessToken denied on resource ...` (host: `iamcredentials.googleapis.com`) | The SA's IAM policy does not include `roles/iam.workloadIdentityUser` for the `principal://.../subject/<sub>` of the incoming identity. | `gcloud iam service-accounts get-iam-policy <sa-email>` |
| 5 | Federation succeeds, impersonation succeeds, Vertex AI returns 403 | `403 PERMISSION_DENIED Permission 'aiplatform.endpoints.predict' denied` (host: `*-aiplatform.googleapis.com`) | The SA is missing `roles/aiplatform.user` on the project (or on the specific Vertex resource). Roles do not transfer between SAs. | `gcloud projects get-iam-policy <project> --flatten="bindings[].members" --filter="bindings.members:serviceAccount:<sa-email>"` |
| 6 | The Entra SDK mints the Agent Identity token, but `aud` is wrong | STS returns `403 The token audience ... is not allowed` even though the OID and provider look right | The Entra SDK has to have a DownstreamApi named `gcp-sts` whose scope is `api://<Blueprint>/.default`. Without that DownstreamApi, the agent's `_AgentIdSidecarSupplier` will fetch from the wrong endpoint and the audience will be wrong. | `az containerapp exec -g <rg> -n <app> --container agent --command env \| grep -i downstream` |
| 7 | The agent code reaches for the VM IMDS URL | The fetch for the input assertion never returns and the request fails with a connection error | ACA does not use VM-style IMDS. The endpoint is `http://localhost:12356/msi/token` with an `X-IDENTITY-HEADER` secret, and the port is dynamic per replica. Let the Azure SDK pick it up from env vars instead of hardcoding. | `az containerapp exec -g <rg> -n <app> --container agent --command env \| grep IDENTITY` |

The signal that separates a federation failure from an impersonation failure from a Vertex permission failure is the **host in the error body**. STS errors come from `sts.googleapis.com` and surface as 400 or 403 with a body that names the validation that failed. Impersonation errors come from `iamcredentials.googleapis.com` and surface as 403 with a body that names `iam.serviceAccounts.getAccessToken`. Vertex errors come from `*-aiplatform.googleapis.com` and surface as 403 with a body that names an `aiplatform.*` permission. When you are paged in the middle of a deployment, the host in the error body tells you which of the three layers to look at first.

The audience-on-wire trap is worth restating one more time, because it accounts for a disproportionate share of first-deployment failures. Entra v2 normalizes any first-party or app-registered identifier URI to its bare appId GUID before signing the token. So `api://<Blueprint>` becomes the Blueprint's bare appId on the wire. The GCP provider compares `aud` byte-for-byte against `allowedAudiences`. Listing only the URI form rejects every token. Always list both forms.


## 11. Related reading

For the UAMI-only variant of this flow, where the UAMI itself federates to GCP and no Agent Identity, Blueprint, or Entra SDK GCP DownstreamApi is involved, see [Outbound Federation Into GCP from Azure with User-Assigned Managed Identity](uami-outbound-federation-gcp.md).

For the Entra-side bootstrap of the Blueprint, the Agent Identity, and the FIC, see [Microsoft Learn: Agent identities](https://learn.microsoft.com/en-us/entra/agent-id/agent-identities) and [Microsoft Learn: Agent identity blueprints](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-blueprint).

For guidance on how many Blueprints and Agent Identities to create for a given workload, see [Microsoft Entra Agent ID design patterns](https://learn.microsoft.com/en-us/entra/agent-id/concept-agent-id-design-patterns) and [Plan your agent identity architecture](https://learn.microsoft.com/en-us/entra/agent-id/how-to-plan-agent-identity-architecture).
