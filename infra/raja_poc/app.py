from aws_cdk import App

from .stacks.avp_stack import AvpStack
from .stacks.rajee_envoy_stack import RajeeEnvoyStack
from .stacks.services_stack import ServicesStack

app = App()

avp_stack = AvpStack(app, "RajaAvpStack")
services_stack = ServicesStack(
    app,
    "RajaServicesStack",
    policy_store_id=avp_stack.policy_store_id,
    policy_store_arn=avp_stack.policy_store_arn,
)

rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    jwt_signing_secret=services_stack.jwt_secret,
)
rajee_envoy_stack.add_dependency(services_stack)

app.synth()
