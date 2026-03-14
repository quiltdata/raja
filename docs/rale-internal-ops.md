# RALE Internal Design/Ops Notes

## Audience
These details are for:

- Client integrators calling RALE endpoints directly.
- Operators debugging Envoy/Lambda routing and authorization behavior.

These details are not required for general RAJA users.

## Deployed Components

- Envoy (RAJEE service) routes RALE traffic.
- RALE Authorizer Lambda issues TAJ tokens.
- RALE Router Lambda validates TAJ tokens and resolves logical keys via manifest cache.

## Request Flow

### 1) Bootstrap request (no TAJ)
Send a request to Envoy with:

- `x-raja-principal: <principal>`

Envoy routes to RALE Authorizer Lambda, which returns a TAJ token payload.

### 2) Data request (with TAJ)
Send subsequent requests with:

- `x-rale-taj: <taj-jwt>`

Envoy routes to RALE Router Lambda, which:

- Validates TAJ signature and claims.
- Validates manifest membership for requested logical key.
- Resolves physical S3 location and fetches object.

## Runtime Routing Conditions

Envoy RALE mode is enabled when both are set in the Envoy container environment:

- `RALE_AUTHORIZER_URL`
- `RALE_ROUTER_URL`

If RALE mode is not enabled, Envoy uses the existing RAJEE JWT+scope Lua authorization path.

## Common Operational Checks

- Verify ECS task definition environment includes RALE URLs.
- Verify `raja-standalone-rale-authorizer` and `raja-standalone-rale-router` are healthy.
- If requests return `401 Jwt is missing`, confirm new Envoy task revision is active and stable.

## Current Terraform Outputs (used by ops/tests)

- `RaleAuthorizerArn`
- `RaleAuthorizerUrl`
- `RaleRouterArn`
- `RaleRouterUrl`
