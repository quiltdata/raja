from __future__ import annotations

from pathlib import Path

from aws_cdk import BundlingOptions, CfnOutput, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from ..constructs.control_plane import ControlPlaneLambda


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

        repo_root = Path(__file__).resolve().parents[3]
        raja_layer = lambda_.LayerVersion(
            self,
            "RajaLayer",
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            code=lambda_.Code.from_asset(
                str(repo_root),
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r infra/raja_poc/layers/raja/requirements.txt "
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

        jwt_secret = secretsmanager.Secret(
            self,
            "JwtSigningKey",
            description="JWT signing secret for RAJA token issuance",
        )

        control_plane = ControlPlaneLambda(
            self,
            "ControlPlane",
            policy_store_id=policy_store_id,
            mappings_table=mappings_table,
            principal_table=principal_table,
            raja_layer=raja_layer,
            jwt_secret=jwt_secret,
            token_ttl=3600,
        )

        api = apigateway.LambdaRestApi(
            self,
            "RajaApi",
            handler=control_plane.function,
            proxy=True,
        )

        self.api_url = api.url

        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "PolicyStoreId", value=policy_store_id)
        CfnOutput(self, "PolicyStoreArn", value=policy_store_arn)
        CfnOutput(self, "ControlPlaneLambdaArn", value=control_plane.function.function_arn)
        CfnOutput(self, "MappingsTableName", value=mappings_table.table_name)
        CfnOutput(self, "PrincipalTableName", value=principal_table.table_name)
