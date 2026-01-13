from aws_cdk import App

from .stacks.avp_stack import AvpStack
from .stacks.services_stack import ServicesStack

app = App()

avp_stack = AvpStack(app, "RajaAvpStack")
ServicesStack(
    app,
    "RajaServicesStack",
    policy_store_id=avp_stack.policy_store_id,
    policy_store_arn=avp_stack.policy_store_arn,
)

app.synth()
