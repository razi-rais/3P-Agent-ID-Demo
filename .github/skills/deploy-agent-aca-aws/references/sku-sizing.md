# SKU and sizing decisions

Before provisioning any Azure resources, confirm each of these SKU choices with the user. Do **not** silently default to the cheapest tier — the defaults below are fine for a one-shot demo but will bite you on iteration, debugging, or production use.

Cost estimates are approximate USD/month (East US 2, April 2026) and assume `minReplicas = 1`.

## 1. Azure Container Registry (ACR) SKU

| SKU | Cost | Storage | Pull throttle | When to use |
|---|---|---|---|---|
| **Basic** | ~$5/mo | 10 GB | 1,000/min per registry | One-shot demo; few rebuilds |
| **Standard** | ~$20/mo | 100 GB | 3,000/min | **Iteration, CI/CD, repeated rebuilds** |
| **Premium** | ~$50/mo | 500 GB | 10,000/min | Geo-replication, private endpoint, content trust |

> **[!WARNING] Silent failure mode**
> A Basic registry with 10+ rapid revision pushes during active development will hit the pull-rate ceiling. Container Apps reports `ImagePullBackOff` with no SKU hint. If you'll iterate more than once a day, use Standard.

## 2. Azure Container Apps workload profile

| Profile | Cost model | Max/replica | Cold start | When to use |
|---|---|---|---|---|
| **Consumption** | Pay per vCPU-second, scale-to-zero capable | 4 vCPU / 8 Gi | ~5–30 s from 0 replicas | Demo, low-traffic apps |
| **Dedicated D4** | ~$140/mo reserved | 4 vCPU / 16 Gi | None when minReplicas ≥ 1 | **Iteration without cold starts** |
| **Dedicated D8 / D16 / D32** | ~$280 / $560 / $1120/mo | 8 / 16 / 32 vCPU | None | Larger container totals |
| **Dedicated GPU (NC24-A100)** | ~$2k+/mo | 24 vCPU + A100 | None | Large local LLMs only (not this AWS deployment) |

> **[!WARNING] Silent failure mode**
> Consumption plan + `minReplicas = 0` is attractive for cost, but the first request after idle triggers a full cold start: image pull + Entra SDK warmup + first Bedrock call. Demos frequently time out (30+ s). Use `minReplicas = 1` on Consumption, or move to Dedicated.

## 3. Logs destination on the ACA environment

| Destination | Cost | What you can query | When to use |
|---|---|---|---|
| **none** | Free | `az containerapp logs show` (console only, last ~20 min) | Short-lived demo |
| **log-analytics** | ~$2.76/GB ingested | Full KQL on `ContainerAppConsoleLogs_CL`, `ContainerAppSystemLogs_CL` | **First deploy, debugging** |
| **azure-monitor** | ~$2.76/GB ingested | Managed destinations (Storage, Event Hub, Log Analytics via DCR) | Multi-destination fan-out |

> **[!WARNING] Silent failure mode**
> `--logs-destination none` means you cannot query sidecar auth errors after the fact. When the `AADSTS*` error is transient and you need to correlate across containers, system logs from `none` are already gone. Always use `log-analytics` for the first deploy and switch off later if cost matters.

## 4. Replica count

| Setting | Behavior | When to use |
|---|---|---|
| `min=1, max=1` | Always on, no autoscale | Demo — predictable behavior |
| `min=1, max=3` | No cold start, autoscale on HTTP concurrency | Iteration, light load |
| `min=0, max=N` | Scale-to-zero, pays only when traffic | Cost-sensitive, cold-start-tolerant |

> **[!WARNING] Silent failure mode**
> Scale-to-zero + four containers means every container cold-starts from scratch on first request. The sidecar's Entra SDK warmup alone is ~3–5 s; boto3's first STS call is another ~1–2 s. Combined: visible hang in the browser. Use `min=1` unless cost forces otherwise.

## 5. Container CPU/memory per replica

ACA requires the **total** of `cpu` and `memory` across all containers in a replica to match a valid consumption combination. Reference: [ACA resource allocation](https://learn.microsoft.com/en-us/azure/container-apps/containers#allocations).

Working total for this AWS deployment: **1.25 vCPU / 2.5 Gi** (llm-agent 0.5/1 + sidecar 0.25/0.5 + weather-api 0.25/0.5 + token-refresher 0.25/0.5).

> **[!WARNING] Silent failure mode**
> Invalid combinations are rejected at deploy time with a long list of valid pairs. If you change any container's sizing, confirm the new totals still match. Common invalid combo: 2.0 vCPU / 2.0 Gi (memory too low for the vCPU count).

## 6. AWS region and Bedrock inference profile

Not strictly a SKU, but a silent-failure knob of the same flavor.

| Setting | Default here | Note |
|---|---|---|
| `AWS_REGION` | `us-east-2` | Must be a region where Bedrock Claude 3 Haiku access is **approved** for the account |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-3-haiku-20240307-v1:0` | The `us.` prefix is the **inference profile**; the plain `anthropic.…` ID will fail in most regions with `ValidationException` |

> **[!WARNING] Silent failure mode**
> Requesting model access in the Bedrock console returns "Access granted" for the base model ID; the inference-profile form requires the region to be in the profile's region group. `us.anthropic.claude-3-haiku-…` works in `us-east-1`, `us-east-2`, `us-west-2`. Elsewhere, switch to a regional non-profile ID.

## Recommended demo defaults (what the orchestrator uses if you do nothing)

| Variable | Value | Why |
|---|---|---|
| `ACR_SKU` | `Basic` | One-shot demo; upgrade if you iterate |
| `ACA_WORKLOAD_PROFILE` | `Consumption` | Cheapest; fine at `minReplicas=1` |
| `LOGS_DESTINATION` | `log-analytics` | You will debug at least once; do not `none` |
| `MIN_REPLICAS` | `1` | No cold starts |
| `MAX_REPLICAS` | `1` | No surprise autoscale |

The orchestrator **requires these to be set explicitly** — it will fail with a clear error if any are missing, so AI agents don't silently pick `Basic`/`none`/`0-1`.
