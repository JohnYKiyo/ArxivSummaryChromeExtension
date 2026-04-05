#!/usr/bin/env bash
# =============================================================================
# deploy.sh - Deploy the arXiv Translator service to AWS
# =============================================================================
#
# Usage:
#   ./infrastructure/deploy.sh              # Full deploy (all steps)
#   ./infrastructure/deploy.sh --backend    # Build & push Docker image + deploy CDK
#   ./infrastructure/deploy.sh --frontend   # Upload frontend to S3 + invalidate CF
#   ./infrastructure/deploy.sh --cdk-only   # Deploy CDK stacks only (no Docker/frontend)
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - AWS CDK CLI installed (npm install -g aws-cdk)
#   - Docker installed and running
#   - Node.js / npm (for frontend build)
#   - Python 3.12+ with pip
#
# Environment variables (optional overrides):
#   AWS_REGION          - AWS region (default: ap-northeast-1)
#   AWS_ACCOUNT_ID      - AWS account ID (auto-detected if not set)
#   GOOGLE_API_KEY      - Google API key (must be set for backend runtime)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CDK_DIR="${SCRIPT_DIR}/cdk"

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")}"
ECR_REPO_NAME="arxiv-translator-backend"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
check_prerequisites() {
    local missing=()
    command -v aws   >/dev/null 2>&1 || missing+=("aws-cli")
    command -v cdk   >/dev/null 2>&1 || missing+=("aws-cdk")
    command -v docker >/dev/null 2>&1 || missing+=("docker")
    command -v python3 >/dev/null 2>&1 || missing+=("python3")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi

    if [[ -z "${AWS_ACCOUNT_ID}" ]]; then
        log_error "Cannot determine AWS account ID. Run 'aws configure' first."
        exit 1
    fi

    log_info "AWS Account: ${AWS_ACCOUNT_ID}"
    log_info "AWS Region:  ${AWS_REGION}"
}

# ---------------------------------------------------------------------------
# Step 1: Install CDK Python dependencies
# ---------------------------------------------------------------------------
install_cdk_deps() {
    log_info "Installing CDK Python dependencies..."
    cd "${CDK_DIR}"
    pip install -q -r requirements.txt
    cd "${PROJECT_ROOT}"
}

# ---------------------------------------------------------------------------
# Step 2: Bootstrap CDK (first-time only)
# ---------------------------------------------------------------------------
bootstrap_cdk() {
    log_info "Bootstrapping CDK (if needed)..."
    cd "${CDK_DIR}"
    cdk bootstrap "aws://${AWS_ACCOUNT_ID}/${AWS_REGION}" 2>/dev/null || true
    cd "${PROJECT_ROOT}"
}

# ---------------------------------------------------------------------------
# Step 3: Deploy CDK stacks
# ---------------------------------------------------------------------------
deploy_cdk() {
    log_info "Deploying CDK stacks..."
    cd "${CDK_DIR}"
    cdk deploy --all --require-approval never --outputs-file "${SCRIPT_DIR}/cdk-outputs.json"
    cd "${PROJECT_ROOT}"
    log_info "CDK stacks deployed. Outputs saved to infrastructure/cdk-outputs.json"
}

# ---------------------------------------------------------------------------
# Step 4: Build and push Docker image to ECR
# ---------------------------------------------------------------------------
build_and_push_docker() {
    local ecr_uri="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

    log_info "Logging in to ECR..."
    aws ecr get-login-password --region "${AWS_REGION}" \
        | docker login --username AWS --password-stdin \
          "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

    log_info "Building Docker image..."
    docker build -t "${ECR_REPO_NAME}:latest" "${PROJECT_ROOT}/backend"

    log_info "Tagging and pushing image to ECR..."
    docker tag "${ECR_REPO_NAME}:latest" "${ecr_uri}:latest"
    docker push "${ecr_uri}:latest"

    log_info "Forcing ECS service update..."
    aws ecs update-service \
        --cluster arxiv-translator \
        --service arxiv-translator-backend \
        --force-new-deployment \
        --region "${AWS_REGION}" \
        >/dev/null

    log_info "Docker image pushed and ECS service updated."
}

# ---------------------------------------------------------------------------
# Step 5: Build and upload frontend to S3
# ---------------------------------------------------------------------------
deploy_frontend() {
    local outputs_file="${SCRIPT_DIR}/cdk-outputs.json"

    if [[ ! -f "${outputs_file}" ]]; then
        log_error "CDK outputs file not found. Run CDK deploy first."
        exit 1
    fi

    # Extract bucket name and distribution ID from CDK outputs
    local bucket_name
    bucket_name=$(python3 -c "
import json, sys
with open('${outputs_file}') as f:
    data = json.load(f)
stack = data.get('ArxivTranslatorStorage', {})
for k, v in stack.items():
    if 'FrontendBucketName' in k:
        print(v)
        sys.exit(0)
print('')
")

    local dist_id
    dist_id=$(python3 -c "
import json, sys
with open('${outputs_file}') as f:
    data = json.load(f)
stack = data.get('ArxivTranslatorStorage', {})
for k, v in stack.items():
    if 'CloudFrontDistributionId' in k:
        print(v)
        sys.exit(0)
print('')
")

    if [[ -z "${bucket_name}" || -z "${dist_id}" ]]; then
        log_error "Could not extract bucket name or distribution ID from CDK outputs."
        exit 1
    fi

    log_info "Building frontend..."
    cd "${PROJECT_ROOT}/frontend/web"
    npm ci
    npm run build
    cd "${PROJECT_ROOT}"

    log_info "Uploading frontend to S3 (${bucket_name})..."
    aws s3 sync "${PROJECT_ROOT}/frontend/web/dist" "s3://${bucket_name}" \
        --delete \
        --region "${AWS_REGION}"

    log_info "Invalidating CloudFront cache (${dist_id})..."
    aws cloudfront create-invalidation \
        --distribution-id "${dist_id}" \
        --paths "/*" \
        >/dev/null

    log_info "Frontend deployed."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local mode="${1:-full}"

    check_prerequisites
    install_cdk_deps

    case "${mode}" in
        --backend)
            bootstrap_cdk
            deploy_cdk
            build_and_push_docker
            ;;
        --frontend)
            deploy_frontend
            ;;
        --cdk-only)
            bootstrap_cdk
            deploy_cdk
            ;;
        full|*)
            bootstrap_cdk
            deploy_cdk
            build_and_push_docker
            deploy_frontend
            ;;
    esac

    log_info "Deployment complete!"

    if [[ -f "${SCRIPT_DIR}/cdk-outputs.json" ]]; then
        echo ""
        log_info "=== Deployment Outputs ==="
        python3 -c "
import json
with open('${SCRIPT_DIR}/cdk-outputs.json') as f:
    data = json.load(f)
for stack, outputs in data.items():
    for key, value in outputs.items():
        print(f'  {key}: {value}')
"
    fi
}

main "$@"
