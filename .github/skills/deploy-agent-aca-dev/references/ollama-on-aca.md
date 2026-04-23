# Ollama on Azure Container Apps

## Image strategy: `baked` vs `runtime-pull`

Ollama by default downloads model weights on first `/api/generate` request. In docker-compose this is fine — the weights end up in a named volume. On ACA ephemeral disk, they vanish on every replica restart and re-pull every cold start.

### Strategy A — `baked` (recommended)

Bake the model into a custom Ollama image:

```dockerfile
FROM ollama/ollama:latest
ENV OLLAMA_HOST=0.0.0.0:11434
RUN ollama serve & \
    sleep 5 && \
    ollama pull qwen2.5:1.5b && \
    pkill ollama
ENTRYPOINT ["ollama", "serve"]
```

- **Pros:** zero first-request delay; model on disk at container start; deterministic replicas
- **Cons:** ACR image size +~1 GB per model; rebuild on model changes
- **Script:** [`build-ollama-image.sh`](../scripts/build-ollama-image.sh) automates this

> **[!IMPORTANT] `baked` requires local Docker Desktop.**
> `az acr build` cannot build baked Ollama images. The Dockerfile's `RUN ollama serve & ollama pull ...` requires a running daemon, which ACR Build's environment does not support — the serve silently fails, the pull hangs, and the image never appears in ACR. If Docker Desktop is unavailable, use `runtime-pull`.

### Strategy B — `runtime-pull`

Use `docker.io/ollama/ollama:latest` directly. Model pulls on first request.

- **Pros:** smaller ACR footprint; zero image rebuilds on model changes
- **Cons:** 30+ s hang on first request per replica; replica restart = re-pull; depends on Ollama registry availability at request time
- **When it's acceptable:** cost-sensitive experiments, non-demo workloads, when you control first-user latency

## Crash-loop diagnostics

### `out of memory`

Ollama loads the entire model into RAM at startup. Table:

| Model | Minimum container memory |
|---|---|
| `qwen2.5:1.5b` | 1.5 Gi |
| `qwen2.5:3b` | 2.5 Gi |
| `qwen2.5:7b` | 6 Gi (GPU recommended) |
| `llama3.2:1b` | 1 Gi |

Bump `ollama` container's `memory:` allocation to match. Remember to also update the replica total to match a valid ACA combo.

### Model not found (404 from pull)

Ollama registry names are case-sensitive and tag-sensitive. `qwen2.5:1.5b` works; `Qwen2.5:1.5B` does not. Verify with `docker run --rm ollama/ollama:latest ollama pull <name>` locally before baking.

### Slow first response even with `baked`

First call after replica start triggers model load from disk into RAM (~3–5 s). This is separate from pull and is unavoidable. Subsequent calls are fast as long as the container stays warm.

## Performance expectations on Consumption

| Model | 0.75 vCPU / 1.5 Gi | 1.0 vCPU / 2 Gi | 2.0 vCPU / 4 Gi |
|---|---|---|---|
| `qwen2.5:1.5b` | 5–20 s | 4–12 s | 2–8 s |
| `llama3.2:1b` | 3–10 s | 2–6 s | 1–4 s |
| `qwen2.5:3b` | 10–30 s | 8–20 s | 5–15 s |
| `qwen2.5:7b` | **timeout** | 30–60 s | 15–30 s |

For consistent sub-5-second responses on 1.5B models, move to a Dedicated-D4 workload profile. For 7B+ with interactive latency, GPU is required.

## Why not Azure OpenAI?

Because this is the **local-LLM** variant. The whole point is to run fully inside Azure Container Apps without any external model provider credentials. For Azure OpenAI, use a different sample (or adapt the AWS one, replacing Bedrock with AOAI).
