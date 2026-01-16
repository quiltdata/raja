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

api_url = services_stack.api_url.rstrip("/")
scheme, rest = api_url.split("://", 1)
netloc = rest.split("/", 1)[0]
issuer = f"{scheme}://{netloc}"
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    jwks_endpoint=f"{api_url}/.well-known/jwks.json",
    raja_issuer=issuer,
)

app.synth()
