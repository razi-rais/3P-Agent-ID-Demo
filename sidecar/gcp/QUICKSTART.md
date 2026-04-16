# Quick Start: Google Vertex AI Agent

Get the Agent Identity demo running with Google Vertex AI in 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- Google Cloud account with billing enabled
- Microsoft Entra Agent Identity configured (see main README)

## Step 1: Enable Vertex AI API

```bash
# Set your project ID
export GCP_PROJECT_ID="your-project-id"

# Enable Vertex AI API
gcloud services enable aiplatform.googleapis.com --project=$GCP_PROJECT_ID
```

## Step 2: Create Service Account

```bash
# Create service account
gcloud iam service-accounts create vertex-ai-agent \
    --display-name="Vertex AI Agent" \
    --project=$GCP_PROJECT_ID

# Grant Vertex AI User role
gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
    --member="serviceAccount:vertex-ai-agent@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

# Create and download key
gcloud iam service-accounts keys create vertex-ai-key.json \
    --iam-account=vertex-ai-agent@${GCP_PROJECT_ID}.iam.gserviceaccount.com
```

## Step 3: Configure Environment

Create or update `.env` file in the `sidecar` directory:

```env
# Microsoft Entra Agent Identity (from main setup)
TENANT_ID=your-tenant-id
BLUEPRINT_APP_ID=your-blueprint-app-id
BLUEPRINT_CLIENT_SECRET=your-secret
AGENT_CLIENT_ID=your-agent-app-id

# Google Vertex AI Configuration
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=us-central1
VERTEXAI_MODEL_ID=gemini-1.5-pro
```

## Step 4: Place Credentials

Move the service account key to the sidecar directory:

```bash
mv vertex-ai-key.json /path/to/3P-Agent-ID-Demo/sidecar/
```

## Step 5: Start Services

```bash
cd /path/to/3P-Agent-ID-Demo/sidecar

# Build and start all services
docker-compose -f docker-compose-google.yml up -d

# Check status
docker-compose -f docker-compose-google.yml ps

# View logs
docker-compose -f docker-compose-google.yml logs -f
```

## Step 6: Test the Demo

Open your browser to: **http://localhost:3002**

Try asking: "What's the weather in Seattle?"

## Verify Setup

### Check Service Health

```bash
# Check sidecar
curl http://localhost:5002/health

# Check weather API
curl http://localhost:8082/health

# Check LLM agent
curl http://localhost:3002/health
```

### Check Vertex AI Status

```bash
docker-compose -f docker-compose-google.yml logs llm-agent-google | grep "Vertex AI"
```

You should see:
```
[Vertex AI] ✓ Vertex AI initialized successfully
```

## Common Issues

### "Vertex AI not available"

1. Verify API is enabled:
   ```bash
   gcloud services list --enabled --project=$GCP_PROJECT_ID | grep aiplatform
   ```

2. Check service account permissions:
   ```bash
   gcloud projects get-iam-policy $GCP_PROJECT_ID \
       --flatten="bindings[].members" \
       --filter="bindings.members:serviceAccount:vertex-ai-agent@*"
   ```

3. Verify key file location:
   ```bash
   ls -la /path/to/3P-Agent-ID-Demo/sidecar/vertex-ai-key.json
   ```

### "Permission denied" errors

Grant additional roles if needed:
```bash
gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
    --member="serviceAccount:vertex-ai-agent@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/aiplatform.admin"
```

### Port conflicts

If ports are already in use, edit `docker-compose-google.yml`:
```yaml
ports:
  - "3003:3000"  # Change 3002 to 3003 or any available port
```

## Stop Services

```bash
docker-compose -f docker-compose-google.yml down
```

## Clean Up

To remove everything including volumes:
```bash
docker-compose -f docker-compose-google.yml down -v
```

## Next Steps

- Switch to **Vertex AI Mode** in the UI to see LLM-powered responses
- Try different cities and weather queries
- View the debug panel to see Agent Identity token flow
- Experiment with different Gemini models in `.env`

## Cost Management

- **Gemini 1.5 Flash** - ~$0.35 per 1M input tokens
- **Gemini 1.5 Pro** - ~$3.50 per 1M input tokens

Set budget alerts in Google Cloud Console to monitor costs.

## Resources

- [Full Documentation](llm-agent-google/README.md)
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/pricing)
- [Agent Identity Guide](SIDECAR-GUIDE.md)
