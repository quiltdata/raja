from __future__ import annotations

from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class EnforcerLambda(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        jwt_secret: secretsmanager.Secret,
    ) -> None:
        super().__init__(scope, construct_id)

        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda_handlers/enforcer"),
            environment={
                "JWT_SECRET_ARN": jwt_secret.secret_arn,
            },
        )

        jwt_secret.grant_read(self.function)
