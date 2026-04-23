# SKU and sizing decisions

Before provisioning any Azure resources, confirm each of these SKU choices with the user. Do **not** silently default to the cheapest tier. The defaults are fine for a one-shot demo but bite you on iteration, debugging, or production use.

Cost estimates are approximate USD/month (East US 2, April 2026) and assume `minReplicas = 1`.

## 1. Azure Container Registry (ACR) SKU

| SKU | Cost | Storage | When to use |
|---|---|---|---|
| **Basic** | ~$5/mo | 10 GB | One-shot demo with one Ollama model |
| **Standard** | ~$20/mo | 100 GB | **Recommended if experimenting with multiple models** (each baked image is ~1 GB) |
| **Premium** | ~$50/mo | 500 GB | Private endpoint, geo-replication |

> **[!WARNING] Silent failure mode**
> Basic's 10 GB fills fast when you baked-bake-bake new model variants. Each `qwen2.5:1.5b`, `llama3.2:1b`, etc. is ~1 GB. Upgrade to Standard during active experimentation.

## 2. Azure Container Apps workload profile

| Profile | Cost | Max/replica | Cold start | When to use |
|---|---|---|---|---|
| **Consumption** | Pay per vCPU-second | 4 vCPU / 8 Gi | ~10–30 s from 0 | Demo, low-traffic |
| **Dedicated D4** | ~$140/mo reserved | 4 vCPU / 16 Gi | None when min=1 | **Snappier 1.5–3B models** |
| **Dedicated D8/D16** | ~$280–$560/mo | 8–16 vCPU | None | Larger models on CPU |
| **Dedicated GPU** | ~$2k+/mo | 24 vCPU + A100 | None | **Required for 7B+ at interactive latency** |

> **[!WARNING] Silent failure mode**
> Qwen 2.5 7B on Consumption (0.75 vCPU) returns in 30–60 s per short answer and often exceeds ACA's default 4-minute ingress timeout. Stay on 1–3B models on Consumption, or move to GPU for 7B+.

## 3. Ollama model

| Model | Size | CPU latency (Consumption) | When to use |
|---|---|---|---|
| **`qwen2.5:1.5b`** | ~1 GB | 5–20 s | **Recommended default** — good balance |
| `qwen2.5:3b` | ~2 GB | 10–30 s | Better quality, still CPU-viable |
| `qwen2.5:7b` | ~4 GB | 30–60 s | **Needs GPU** for interactive latency |
| `llama3.2:1b` | ~1 GB | 3–10 s | Fastest, weakest quality |
| `phi3:mini` | ~2 GB | 8–20 s | Good Microsoft-native option |

> **[!WARNING] Silent failure mode**
> Pulling a 7B model on a Consumption profile without upsizing memory causes Ollama to crash with `out of memory` — and with `LOGS_DESTINATION=none` you won't see the crash. Always pair model choice with matching memory.

## 4. Ollama image strategy

| Strategy | Image size | First-request latency | When to use |
|---|---|---|---|
| **`baked`** | ACR image + ~1 GB model layer | Fast (model already on disk) | **Recommended for demos** |
| `runtime-pull` | ~200 MB (Ollama only) | ~30 s extra on first request per replica | Cost-sensitive, not demo |

> **[!WARNING] Silent failure mode**
> `runtime-pull` + `minReplicas=0` means every cold start pulls the model again. User waits 30+ s for the first answer. Compound mistake; don't combine.

## 5. Logs destination

| Destination | Cost | What you can query | When to use |
|---|---|---|---|
| **none** | Free | Last ~20 min of console | Short-lived demo |
| **log-analytics** | ~$2.76/GB | Full KQL on `ContainerAppSystemLogs_CL` | **Recommended — Ollama crashes and sidecar auth errors need this** |
| **azure-monitor** | ~$2.76/GB | Managed destinations | Multi-destination fan-out |

> **[!WARNING] Silent failure mode**
> Ollama OOM, model-pull 404s, and sidecar `AADSTS*` errors can all be transient. With `none`, the logs are gone by the time you realize something is wrong.

## 6. Replicas

| Setting | Behavior | When to use |
|---|---|---|
| `min=1, max=1` | Always on | **Demo default** — no cold start |
| `min=1, max=3` | Autoscale on HTTP concurrency | Light load |
| `min=0, max=N` | Scale-to-zero | **Not recommended** with Ollama — model reloads into RAM every cold start |

## 7. Container CPU/memory per replica

The `containerapp.yaml.template` uses variables `OLLAMA_CPU` and `OLLAMA_MEMORY` for the Ollama container (other containers are fixed: llm-agent 0.5/1, sidecar 0.25/0.5, weather-api 0.25/0.5). **You MUST set these based on your model choice:**

| Model | `OLLAMA_CPU` | `OLLAMA_MEMORY` | Replica total (CPU/Mem) | Valid on |
|---|---|---|---|---|
| `qwen2.5:1.5b` / `llama3.2:1b` | `0.75` | `1.5Gi` | 1.75 / 3.5Gi | Consumption or Dedicated |
| `qwen2.5:3b` | `1.0` | `2.5Gi` | 2.0 / 4.5Gi | Consumption or Dedicated |
| `qwen2.5:7b` | `2.0` | `6Gi` | 3.0 / 8.5Gi | Dedicated-D8+ or GPU only |

> **[!WARNING] Silent failure mode**
> If `OLLAMA_MEMORY` is undersized for the chosen model (e.g., 1.5Gi for `qwen2.5:3b`), Ollama will crash-loop with `out of memory` — and with `LOGS_DESTINATION=none` you won't see it. Always pair model choice with the matching memory allocation from the table above.

## Recommended demo defaults (what the orchestrator enforces)

| Variable | Value |
|---|---|
| `ACR_SKU` | `Basic` |
| `ACA_WORKLOAD_PROFILE` | `Consumption` |
| `LOGS_DESTINATION` | `log-analytics` |
| `MIN_REPLICAS` | `1` |
| `MAX_REPLICAS` | `1` |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` |
| `OLLAMA_IMAGE_STRATEGY` | `baked` |
| `OLLAMA_CPU` | `0.75` |
| `OLLAMA_MEMORY` | `1.5Gi` |

The orchestrator **requires all of these to be set explicitly** — it fails with a clear error if any is missing, so AI agents don't silently pick defaults.
