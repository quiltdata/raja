from __future__ import annotations

from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class CompilerLambda(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        policy_store_id: str,
        mappings_table: dynamodb.Table,
        principal_table: dynamodb.Table,
        raja_layer: lambda_.ILayerVersion,
    ) -> None:
        super().__init__(scope, construct_id)

        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambda_handlers/compiler"),
            layers=[raja_layer],
            environment={
                "POLICY_STORE_ID": policy_store_id,
                "MAPPINGS_TABLE": mappings_table.table_name,
                "PRINCIPAL_TABLE": principal_table.table_name,
            },
        )

        mappings_table.grant_read_write_data(self.function)
        principal_table.grant_read_write_data(self.function)

        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "verifiedpermissions:ListPolicies",
                    "verifiedpermissions:GetPolicy",
                    "verifiedpermissions:GetPolicyStore",
                ],
                resources=["*"],
            )
        )
