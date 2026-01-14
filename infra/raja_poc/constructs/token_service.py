from __future__ import annotations

from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class TokenServiceLambda(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
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
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambda_handlers/token_service"),
            layers=[raja_layer],
            environment={
                "PRINCIPAL_TABLE": principal_table.table_name,
                "JWT_SECRET_ARN": jwt_secret.secret_arn,
                "TOKEN_TTL": str(token_ttl),
            },
        )

        principal_table.grant_read_data(self.function)
        jwt_secret.grant_read(self.function)
