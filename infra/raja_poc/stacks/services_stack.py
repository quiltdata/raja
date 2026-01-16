from __future__ import annotations

from pathlib import Path

from aws_cdk import BundlingOptions, CfnOutput, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from ..constructs.control_plane import ControlPlaneLambda
from ..utils.platform import detect_platform


class ServicesStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        policy_store_id: str,
        policy_store_arn: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Detect platform at synth time
        _, _, lambda_arch = detect_platform()

        repo_root = Path(__file__).resolve().parents[3]
        asset_excludes = [
            ".git",
            ".venv",
            "infra/cdk.out",
            "infra/cdk.out/**",
            "infra/cdk.out.*",
            "infra/cdk.out.*/**",
            "infra/cdk.out.deploy",
            "infra/cdk.out.deploy/**",
        ]
        raja_layer = lambda_.LayerVersion(
            self,
            "RajaLayer",
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_arch],
            code=lambda_.Code.from_asset(
                str(repo_root),
                exclude=asset_excludes,
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install --no-cache-dir --default-timeout=120 --retries=3 "
                        "-r infra/raja_poc/layers/raja/requirements.txt "
                        "-t /asset-output/python "
                        "&& cp -r src/raja /asset-output/python/raja",
                    ],
                ),
            ),
            description="Shared Raja library for Lambda handlers",
        )

        mappings_table = dynamodb.Table(
            self,
            "PolicyScopeMappings",
            partition_key=dynamodb.Attribute(name="policy_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        principal_table = dynamodb.Table(
            self,
            "PrincipalScopes",
            partition_key=dynamodb.Attribute(name="principal", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        audit_table = dynamodb.Table(
            self,
            "AuditLog",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="event_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
        )

        jwt_secret = secretsmanager.Secret(
            self,
            "JwtSigningKey",
            description="JWT signing secret for RAJA token issuance",
        )

        harness_secret = secretsmanager.Secret(
            self,
            "HarnessSigningKey",
            description="S3 harness signing secret for RAJA S3 authorization tokens",
        )

        self.jwt_secret = jwt_secret
        self.harness_secret = harness_secret

        control_plane = ControlPlaneLambda(
            self,
            "ControlPlane",
            policy_store_id=policy_store_id,
            policy_store_arn=policy_store_arn,
            mappings_table=mappings_table,
            principal_table=principal_table,
            audit_table=audit_table,
            raja_layer=raja_layer,
            jwt_secret=jwt_secret,
            harness_secret=harness_secret,
            token_ttl=3600,
        )

        api = apigateway.RestApi(
            self,
            "RajaApi",
            deploy_options=apigateway.StageOptions(stage_name="prod"),
        )
        api.root.add_method("ANY", apigateway.LambdaIntegration(control_plane.function))
        api.root.add_proxy(
            default_integration=apigateway.LambdaIntegration(control_plane.function),
            any_method=True,
        )
        self.api_url = api.url

        CfnOutput(self, "ApiUrl", value=self.api_url)
        CfnOutput(self, "PolicyStoreId", value=policy_store_id)
        CfnOutput(self, "PolicyStoreArn", value=policy_store_arn)
        CfnOutput(self, "ControlPlaneLambdaArn", value=control_plane.function.function_arn)
        CfnOutput(self, "MappingsTableName", value=mappings_table.table_name)
        CfnOutput(self, "PrincipalTableName", value=principal_table.table_name)
        CfnOutput(self, "AuditTableName", value=audit_table.table_name)
        CfnOutput(self, "JWTSecretArn", value=jwt_secret.secret_arn)
        CfnOutput(self, "HarnessSecretArn", value=harness_secret.secret_arn)
