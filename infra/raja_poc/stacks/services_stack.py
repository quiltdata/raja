from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from ..constructs.compiler_lambda import CompilerLambda
from ..constructs.enforcer_lambda import EnforcerLambda
from ..constructs.token_service import TokenServiceLambda


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

        compiler_lambda = CompilerLambda(
            self,
            "CompilerLambda",
            policy_store_id=policy_store_id,
            mappings_table=mappings_table,
            principal_table=principal_table,
        )

        token_service_lambda = TokenServiceLambda(
            self,
            "TokenServiceLambda",
            principal_table=principal_table,
            jwt_secret=jwt_secret,
            token_ttl=3600,
        )

        enforcer_lambda = EnforcerLambda(
            self,
            "EnforcerLambda",
            jwt_secret=jwt_secret,
        )

        health_code = (
            'def lambda_handler(event, context):\n    return {"statusCode": 200, "body": "ok"}\n'
        )

        health_lambda = lambda_.Function(
            self,
            "HealthLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_inline(health_code),
        )

        api = apigateway.RestApi(self, "RajaApi")

        token_resource = api.root.add_resource("token")
        token_resource.add_method(
            "POST", apigateway.LambdaIntegration(token_service_lambda.function)
        )

        authorize_resource = api.root.add_resource("authorize")
        authorize_resource.add_method(
            "POST", apigateway.LambdaIntegration(enforcer_lambda.function)
        )

        health_resource = api.root.add_resource("health")
        health_resource.add_method("GET", apigateway.LambdaIntegration(health_lambda))

        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "PolicyStoreId", value=policy_store_id)
        CfnOutput(self, "PolicyStoreArn", value=policy_store_arn)
        CfnOutput(self, "CompilerLambdaArn", value=compiler_lambda.function.function_arn)
        CfnOutput(self, "TokenServiceLambdaArn", value=token_service_lambda.function.function_arn)
        CfnOutput(self, "EnforcerLambdaArn", value=enforcer_lambda.function.function_arn)
        CfnOutput(self, "MappingsTableName", value=mappings_table.table_name)
        CfnOutput(self, "PrincipalTableName", value=principal_table.table_name)
