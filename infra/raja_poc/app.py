from aws_cdk import App

from .stacks.avp_stack import AvpStack
from .stacks.services_stack import ServicesStack
from .stacks.web_stack import WebStack

app = App()

avp_stack = AvpStack(app, "RajaAvpStack")
services_stack = ServicesStack(
    app,
    "RajaServicesStack",
    policy_store_id=avp_stack.policy_store_id,
    policy_store_arn=avp_stack.policy_store_arn,
)

web_stack = WebStack(
    app,
    "RajaWebStack",
    api_url=services_stack.api_url,
)

app.synth()
