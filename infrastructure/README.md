# Infrastructure - arXiv Translator

AWS infrastructure managed with CDK (Python) for the arXiv Translator service.

## Architecture

```
                          +------------------+
                          |   CloudFront     |
                          |   (HTTPS CDN)    |
                          +--------+---------+
                                   |
                          +--------+---------+
                          |   S3 Bucket      |
                          |   (Frontend)     |
                          +------------------+

   Users
     |
     |  HTTPS
     v
+----+----+       +------------------+       +------------------+
|   ALB   | ----> |  ECS Fargate     | ----> |  S3 Bucket       |
| (300s   |       |  (FastAPI)       |       |  (ZIP Output)    |
|  idle)  |       |  Private Subnet  |       |  1h lifecycle    |
+---------+       +--------+---------+       +------------------+
  Public                   |
  Subnet                   v
                  +------------------+
                  |  Cognito         |
                  |  User Pool       |
                  +------------------+

VPC (2 AZs)
+----------------------------------------------+
|  Public Subnets   |   Private Subnets        |
|  - ALB            |   - ECS Tasks            |
|  - NAT GW (x1)   |                           |
+----------------------------------------------+
```

## Stacks

| Stack | Resources |
|-------|-----------|
| `ArxivTranslatorNetwork` | VPC, subnets (public/private x2 AZs), NAT Gateway |
| `ArxivTranslatorAuth` | Cognito User Pool, web client, hosted UI domain |
| `ArxivTranslatorStorage` | S3 (frontend), S3 (ZIP output, 1h TTL), CloudFront |
| `ArxivTranslatorBackend` | ECR, ECS cluster, Fargate service, ALB (300s timeout) |

## Prerequisites

- **AWS CLI** v2 configured with credentials (`aws configure`)
- **AWS CDK** v2 (`npm install -g aws-cdk`)
- **Python** 3.12+
- **Docker** (running)
- **Node.js** 18+ and npm (for frontend build)

## Setup

### 1. Install CDK dependencies

```bash
cd infrastructure/cdk
pip install -r requirements.txt
```

### 2. Bootstrap CDK (first time only)

```bash
cdk bootstrap aws://ACCOUNT_ID/ap-northeast-1
```

### 3. Deploy all stacks

```bash
# Full deployment (CDK + Docker + frontend)
./infrastructure/deploy.sh

# CDK stacks only (no Docker build or frontend upload)
./infrastructure/deploy.sh --cdk-only

# Backend only (CDK + Docker image push)
./infrastructure/deploy.sh --backend

# Frontend only (S3 upload + CloudFront invalidation)
./infrastructure/deploy.sh --frontend
```

## Environment Variables

Set these before deploying or configure them in the ECS task definition:

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_REGION` | AWS region | `ap-northeast-1` |
| `AWS_ACCOUNT_ID` | AWS account (auto-detected) | - |
| `GOOGLE_API_KEY` | Google API key for Gemini LLM | *required at runtime* |
| `CDK_DEFAULT_ACCOUNT` | CDK deploy target account | - |
| `CDK_DEFAULT_REGION` | CDK deploy target region | `ap-northeast-1` |

### Setting the Google API Key

The `GOOGLE_API_KEY` is not baked into the CDK stack for security reasons.
After the initial deploy, add it as an environment variable to the ECS task:

```bash
# Option 1: Store in AWS Secrets Manager and reference in task definition
aws secretsmanager create-secret \
    --name arxiv-translator/google-api-key \
    --secret-string "your-google-api-key"

# Option 2: Set directly via ECS console or update the task definition
```

## Deployment Outputs

After deployment, the following values are printed and saved to `infrastructure/cdk-outputs.json`:

- **ALB URL** - Backend API endpoint (`http://<alb-dns>/api/v1/...`)
- **CloudFront Domain** - Frontend URL (`https://<cf-domain>`)
- **Cognito User Pool ID** - For backend/frontend auth config
- **Cognito Client ID** - For frontend auth config
- **ECR Repository URI** - For Docker image pushes
- **S3 Bucket Names** - Frontend and ZIP output buckets

## Key Design Decisions

- **ALB over API Gateway**: API Gateway has a 29-second timeout limit, incompatible with SSE streaming that can run for several minutes during paper processing.
- **ALB idle timeout = 300s**: SSE connections stay open for the duration of paper conversion (up to 3 minutes).
- **Single NAT Gateway**: Cost optimization for non-critical workloads. For production HA, increase to one per AZ.
- **ECS Fargate over Lambda**: SSE requires persistent connections; Lambda's 15-minute max and no streaming response make it unsuitable.
- **S3 ZIP lifecycle = 1 hour**: Temporary output files are auto-deleted to minimize storage cost.

## Cost Estimation (ap-northeast-1)

Approximate monthly cost for minimal configuration (1 Fargate task, low traffic):

| Service | Estimated Cost |
|---------|---------------|
| NAT Gateway | ~$35/mo + data |
| ECS Fargate (0.5 vCPU, 1 GB) | ~$15/mo |
| ALB | ~$18/mo + LCU |
| CloudFront | ~$1/mo (low traffic) |
| S3 | < $1/mo |
| Cognito | Free tier (50k MAU) |
| ECR | < $1/mo |
| CloudWatch Logs | ~$1/mo |
| **Total** | **~$70-80/mo** |

To reduce costs further, consider stopping the Fargate service when not in use.

## Cleanup

Remove all deployed resources:

```bash
cd infrastructure/cdk
cdk destroy --all
```

This will delete all stacks and resources. S3 buckets are configured with `RemovalPolicy.DESTROY` and `auto_delete_objects=True` so they will be cleaned up automatically.

## Useful CDK Commands

```bash
cd infrastructure/cdk

cdk synth          # Synthesize CloudFormation templates
cdk diff           # Show pending changes
cdk deploy --all   # Deploy all stacks
cdk destroy --all  # Tear down all stacks
cdk ls             # List all stacks
```
