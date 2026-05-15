"""Backend stack for the arXiv Translator service.

Creates:
- DynamoDB table for job state management
- Lambda function for API handling (FastAPI via Mangum)
- Lambda function for pipeline execution
- API Gateway HTTP API
- CloudWatch log groups
- IAM roles and permissions
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Size,
    Stack,
    Tags,
    aws_apigatewayv2 as apigwv2,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
)
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct


class BackendStack(Stack):
    """Serverless backend: Lambda + API Gateway + DynamoDB."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        user_pool_id: str,
        user_pool_client_id: str,
        zip_bucket: s3.IBucket,
        cloudfront_domain: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------------------------------------
        # DynamoDB Table for job state
        # -----------------------------------------------------------
        self.jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name="arxiv-translator-jobs",
            partition_key=dynamodb.Attribute(
                name="job_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )

        Tags.of(self.jobs_table).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # Shared Lambda environment variables
        # -----------------------------------------------------------
        common_env = {
            "APP_ENV": "production",
            "LOG_LEVEL": "INFO",
            "AWS_REGION_NAME": Stack.of(self).region,
            "COGNITO_USER_POOL_ID": user_pool_id,
            "COGNITO_APP_CLIENT_ID": user_pool_client_id,
            "S3_BUCKET_NAME": zip_bucket.bucket_name,
            "DYNAMODB_TABLE_NAME": self.jobs_table.table_name,
            "CORS_ORIGINS": f"https://{cloudfront_domain}",
        }

        # -----------------------------------------------------------
        # CloudWatch Log Groups
        # -----------------------------------------------------------
        api_log_group = logs.LogGroup(
            self,
            "ApiLogGroup",
            log_group_name="/lambda/arxiv-translator-api",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        pipeline_log_group = logs.LogGroup(
            self,
            "PipelineLogGroup",
            log_group_name="/lambda/arxiv-translator-pipeline",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------------------------------------
        # Pipeline Lambda (long-running, invoked async)
        # -----------------------------------------------------------
        self.pipeline_lambda = lambda_.Function(
            self,
            "PipelineLambda",
            function_name="arxiv-translator-pipeline",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="src.pipeline_handler.handler",
            code=lambda_.Code.from_asset(
                "../backend",
                exclude=["tests", "*.pyc", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache"],
            ),
            memory_size=1024,
            timeout=Duration.minutes(15),
            ephemeral_storage_size=Size.mebibytes(2048),
            environment={
                **common_env,
                "PIPELINE_LAMBDA_NAME": "",  # Not needed in pipeline Lambda itself
            },
            log_group=pipeline_log_group,
        )

        # -----------------------------------------------------------
        # API Lambda (short-lived, handles HTTP requests)
        # -----------------------------------------------------------
        self.api_lambda = lambda_.Function(
            self,
            "ApiLambda",
            function_name="arxiv-translator-api",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="src.main.handler",
            code=lambda_.Code.from_asset(
                "../backend",
                exclude=["tests", "*.pyc", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache"],
            ),
            memory_size=256,
            timeout=Duration.seconds(29),
            environment={
                **common_env,
                "PIPELINE_LAMBDA_NAME": self.pipeline_lambda.function_name,
            },
            log_group=api_log_group,
        )

        # -----------------------------------------------------------
        # IAM Permissions
        # -----------------------------------------------------------

        # Both Lambdas: DynamoDB read/write
        self.jobs_table.grant_read_write_data(self.api_lambda)
        self.jobs_table.grant_read_write_data(self.pipeline_lambda)

        # Both Lambdas: S3 read/write for ZIP files
        zip_bucket.grant_read_write(self.api_lambda)
        zip_bucket.grant_read_write(self.pipeline_lambda)

        # API Lambda: invoke pipeline Lambda
        self.pipeline_lambda.grant_invoke(self.api_lambda)

        # -----------------------------------------------------------
        # API Gateway HTTP API
        # -----------------------------------------------------------
        api_integration = HttpLambdaIntegration(
            "ApiIntegration",
            self.api_lambda,
        )

        self.http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name="arxiv-translator-api",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=[f"https://{cloudfront_domain}", "http://localhost:5173"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["*"],
                max_age=Duration.hours(1),
            ),
        )

        self.http_api.add_routes(
            path="/api/v1/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=api_integration,
        )

        Tags.of(self.http_api).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(
            self,
            "ApiUrl",
            value=self.http_api.url or "",
            description="API Gateway URL (backend API endpoint)",
            export_name="ArxivTranslatorApiUrl",
        )
        CfnOutput(
            self,
            "ApiLambdaName",
            value=self.api_lambda.function_name,
            description="API Lambda function name",
        )
        CfnOutput(
            self,
            "PipelineLambdaName",
            value=self.pipeline_lambda.function_name,
            description="Pipeline Lambda function name",
        )
        CfnOutput(
            self,
            "DynamoDbTableName",
            value=self.jobs_table.table_name,
            description="DynamoDB table name for job state",
        )
