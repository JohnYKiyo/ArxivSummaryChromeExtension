#!/usr/bin/env python3
"""CDK app entry point for the arXiv Translator infrastructure.

Instantiates and wires together the three stacks:
  AuthStack     -> Cognito
  StorageStack  -> S3 + CloudFront
  BackendStack  -> Lambda + API Gateway + DynamoDB
"""

import os

import aws_cdk as cdk

from stacks import AuthStack, BackendStack, StorageStack

app = cdk.App()

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "ap-northeast-1"),
)

# Common tags applied to every resource in every stack
common_tags = {
    "Project": "arxiv-translator",
    "ManagedBy": "cdk",
}

# ---------------------------------------------------------------------------
# Stacks
# ---------------------------------------------------------------------------

auth_stack = AuthStack(
    app,
    "ArxivTranslatorAuth",
    env=env,
)

storage_stack = StorageStack(
    app,
    "ArxivTranslatorStorage",
    env=env,
)

backend_stack = BackendStack(
    app,
    "ArxivTranslatorBackend",
    env=env,
    user_pool_id=auth_stack.user_pool.user_pool_id,
    user_pool_client_id=auth_stack.user_pool_client.user_pool_client_id,
    zip_bucket=storage_stack.zip_bucket,
    cloudfront_domain=storage_stack.distribution.distribution_domain_name,
)

# Explicit dependencies
backend_stack.add_dependency(auth_stack)
backend_stack.add_dependency(storage_stack)

# ---------------------------------------------------------------------------
# Apply common tags to all stacks
# ---------------------------------------------------------------------------
for key, value in common_tags.items():
    cdk.Tags.of(app).add(key, value)

app.synth()
