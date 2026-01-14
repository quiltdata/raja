from __future__ import annotations

from aws_cdk import BundlingOptions, Duration
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class ControlPlaneLambda(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        policy_store_id: str,
        mappings_table: dynamodb.Table,
        principal_table: dynamodb.Table,
        raja_layer: lambda_.ILayerVersion,
        jwt_secret: secretsmanager.Secret,
        token_ttl: int,
    ) -> None:
        super().__init__(scope, construct_id)

        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            timeout=Duration.seconds(15),
            memory_size=512,
            code=lambda_.Code.from_asset(
                "../lambda_handlers/control_plane",
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output "
                        "&& cp -r . /asset-output",
                    ],
                ),
            ),
            layers=[raja_layer],
            environment={
                "POLICY_STORE_ID": policy_store_id,
                "MAPPINGS_TABLE": mappings_table.table_name,
                "PRINCIPAL_TABLE": principal_table.table_name,
                "JWT_SECRET_ARN": jwt_secret.secret_arn,
                "TOKEN_TTL": str(token_ttl),
            },
        )

        mappings_table.grant_read_write_data(self.function)
        principal_table.grant_read_write_data(self.function)
        jwt_secret.grant_read(self.function)
