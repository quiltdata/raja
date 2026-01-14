"""CDK Construct for static website hosting with S3 and CloudFront."""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct


class StaticSite(Construct):
    """Construct for hosting a static website on S3 with CloudFront distribution.

    This construct creates:
    - S3 bucket for static content
    - CloudFront distribution with HTTPS
    - Automatic deployment of local files to S3
    - Origin Access Identity for secure S3 access
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        site_path: str,
        config_content: str | None = None,
        **kwargs: object,
    ) -> None:
        """Initialize the StaticSite construct.

        Args:
            scope: CDK construct scope
            construct_id: Construct identifier
            site_path: Path to the directory containing static website files
            config_content: Optional JavaScript config content to inject (for config.js)
            **kwargs: Additional keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        # Create S3 bucket for website hosting
        website_bucket = s3.Bucket(
            self,
            "WebsiteBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Create CloudFront distribution
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(website_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
            ],
        )

        # Deploy website files to S3
        s3deploy.BucketDeployment(
            self,
            "DeployWebsite",
            sources=[s3deploy.Source.asset(site_path)],
            destination_bucket=website_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        # If config content provided, create config.js file
        if config_content:
            s3deploy.BucketDeployment(
                self,
                "DeployConfig",
                sources=[s3deploy.Source.data("config.js", config_content)],
                destination_bucket=website_bucket,
                distribution=distribution,
                distribution_paths=["/config.js"],
            )

        # Store references
        self.bucket = website_bucket
        self.distribution = distribution
        self.url = f"https://{distribution.distribution_domain_name}"

        # Output the CloudFront URL
        CfnOutput(
            self,
            "WebsiteUrl",
            value=self.url,
            description="CloudFront distribution URL for the static website",
        )

        CfnOutput(
            self,
            "BucketName",
            value=website_bucket.bucket_name,
            description="S3 bucket name for the static website",
        )

        CfnOutput(
            self,
            "DistributionId",
            value=distribution.distribution_id,
            description="CloudFront distribution ID",
        )
