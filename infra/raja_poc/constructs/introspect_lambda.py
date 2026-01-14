from __future__ import annotations

from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class IntrospectLambda(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        raja_layer: lambda_.ILayerVersion,
    ) -> None:
        super().__init__(scope, construct_id)

        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambda_handlers/introspect"),
            layers=[raja_layer],
        )
