"""Storage stack for the arXiv Translator service.

Creates:
- S3 bucket for frontend static hosting
- S3 bucket for temporary ZIP output (1-hour lifecycle)
- CloudFront distribution for frontend delivery
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
)
from constructs import Construct


class StorageStack(Stack):
    """S3 buckets and CloudFront distribution."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------------------------------------
        # Frontend Hosting Bucket
        # -----------------------------------------------------------
        self.frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=None,  # Auto-generated unique name
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        Tags.of(self.frontend_bucket).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # ZIP Output Bucket (temporary storage, 1-hour lifecycle)
        # -----------------------------------------------------------
        self.zip_bucket = s3.Bucket(
            self,
            "ZipOutputBucket",
            bucket_name=None,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="DeleteAfterOneHour",
                    expiration=Duration.hours(1),
                    enabled=True,
                ),
            ],
        )

        Tags.of(self.zip_bucket).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # CloudFront - Origin Access Identity
        # -----------------------------------------------------------
        oai = cloudfront.OriginAccessIdentity(
            self,
            "FrontendOAI",
            comment="OAI for arXiv Translator frontend",
        )
        self.frontend_bucket.grant_read(oai)

        # -----------------------------------------------------------
        # CloudFront Distribution
        # -----------------------------------------------------------
        self.distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            comment="arXiv Translator frontend",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.frontend_bucket,
                    origin_access_identity=oai,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            ),
            # SPA fallback: serve index.html for 403/404 from S3
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        Tags.of(self.distribution).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(
            self,
            "FrontendBucketName",
            value=self.frontend_bucket.bucket_name,
            description="S3 bucket for frontend static files",
            export_name="ArxivTranslatorFrontendBucket",
        )
        CfnOutput(
            self,
            "ZipBucketName",
            value=self.zip_bucket.bucket_name,
            description="S3 bucket for temporary ZIP output",
            export_name="ArxivTranslatorZipBucket",
        )
        CfnOutput(
            self,
            "CloudFrontDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain",
            export_name="ArxivTranslatorCloudFrontDomain",
        )
        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID (for cache invalidation)",
            export_name="ArxivTranslatorCloudFrontDistId",
        )
