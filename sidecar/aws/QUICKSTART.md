# Quick Start Guide - AWS Bedrock Edition

This guide helps you quickly set up and run the AWS Bedrock version of the Agent Identity demo.

## 1. Prerequisites

- Completed Agent Identity setup (Blueprint + Agent created)
- AWS account with Bedrock access
- AWS credentials (Access Key ID + Secret Access Key)

## 2. Configure AWS Credentials

Edit your `.env` file and add:

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key-here
AWS_SECRET_ACCESS_KEY=your-secret-key-here
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

## 3. Enable Bedrock Models

1. Go to AWS Console → Bedrock
2. Click "Model access" in left menu
3. Click "Manage model access"
4. Enable "Claude 3 Sonnet" and "Claude 3 Haiku"
5. Click "Save changes"

## 4. Start the Demo

```bash
cd sidecar
docker-compose -f docker-compose-aws.yml up -d
```

## 5. Open Browser

Navigate to: **http://localhost:3001**

## 6. Test the Demo

Try asking:
- "What's the weather in Dallas?"
- "How's the weather in Tokyo?"
- "Tell me about the weather in London"

Watch the debug panel on the right to see the complete Agent Identity token flow!

## Stopping the Demo

```bash
docker-compose -f docker-compose-aws.yml down
```

## Troubleshooting

**Q: "AWS credentials not configured" error**
- Check your `.env` file has all AWS_ variables set
- Verify credentials are correct in AWS IAM console

**Q: "Model not found" error**
- Ensure you've enabled model access in Bedrock console
- Try using Claude 3 Haiku: `anthropic.claude-3-haiku-20240307-v1:0`

**Q: Port 3001 already in use**
- Stop the regular Ollama demo: `docker-compose down`
- Or change port in docker-compose-aws.yml

## Running Both Demos

You can run both Ollama (port 3000) and Bedrock (port 3001) simultaneously:

```bash
# Terminal 1
docker-compose up -d

# Terminal 2  
docker-compose -f docker-compose-aws.yml up -d
```

Then open:
- Ollama version: http://localhost:3000
- Bedrock version: http://localhost:3001
