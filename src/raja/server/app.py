from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any, Literal

import boto3
import jwt
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from raja import compile_policy, create_token

JWT_SECRET_ARN = os.environ.get("JWT_SECRET_ARN")
POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID")
MAPPINGS_TABLE = os.environ.get("MAPPINGS_TABLE")
PRINCIPAL_TABLE = os.environ.get("PRINCIPAL_TABLE")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))
HARNESS_ISSUER_DEFAULT = "https://raja.local"
HARNESS_AUDIENCE_DEFAULT = "raja-s3"
HARNESS_TTL_DEFAULT = 300

_jwt_secret_cache: str | None = None
_harness_secret_cache: str | None = None


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("AWS_REGION is required")
    return region


def _require_env(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _avp_client() -> Any:
    return boto3.client("verifiedpermissions", region_name=_get_region())


def _dynamodb_resource() -> Any:
    return boto3.resource("dynamodb", region_name=_get_region())


def _principal_table() -> Any:
    table_name = _require_env(PRINCIPAL_TABLE, "PRINCIPAL_TABLE")
    return _dynamodb_resource().Table(table_name)


def _mappings_table() -> Any:
    table_name = _require_env(MAPPINGS_TABLE, "MAPPINGS_TABLE")
    return _dynamodb_resource().Table(table_name)


def _get_jwt_secret() -> str:
    global _jwt_secret_cache
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache
    secret_arn = _require_env(JWT_SECRET_ARN, "JWT_SECRET_ARN")
    client = boto3.client("secretsmanager", region_name=_get_region())
    response = client.get_secret_value(SecretId=secret_arn)
    _jwt_secret_cache = response["SecretString"]
    return _jwt_secret_cache


def _harness_issuer() -> str:
    return os.environ.get("RAJ_HARNESS_ISSUER", HARNESS_ISSUER_DEFAULT)


def _harness_audience() -> str:
    return os.environ.get("RAJ_HARNESS_AUDIENCE", HARNESS_AUDIENCE_DEFAULT)


def _harness_ttl() -> int:
    return int(os.environ.get("RAJ_HARNESS_TTL", str(HARNESS_TTL_DEFAULT)))


def _get_harness_secret() -> str:
    global _harness_secret_cache
    if _harness_secret_cache is not None:
        return _harness_secret_cache
    secret = os.environ.get("RAJ_HARNESS_SECRET")
    if secret:
        _harness_secret_cache = secret
        return secret
    _harness_secret_cache = secrets.token_urlsafe(32)
    return _harness_secret_cache


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _secret_kid(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return digest[:16]


def _jwks_for_secret(secret: str) -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": _secret_kid(secret),
                "use": "sig",
                "alg": "HS256",
                "k": _b64url(secret.encode("utf-8")),
            }
        ]
    }


def _s3_authority_payload(resource: S3Resource) -> dict[str, str]:
    payload = {"bucket": resource.bucket}
    if resource.key is not None:
        payload["key"] = resource.key
    if resource.prefix is not None:
        payload["prefix"] = resource.prefix
    return payload


def _s3_resource_allows(authority: S3Resource, bucket: str, key: str) -> bool:
    if authority.bucket != bucket:
        return False
    if authority.key is not None:
        return key == authority.key
    if authority.prefix is not None:
        return key.startswith(authority.prefix)
    return False


class TokenRequest(BaseModel):
    principal: str


class S3Resource(BaseModel):
    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None

    @model_validator(mode="after")
    def _validate_selector(self) -> S3Resource:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3MintRequest(BaseModel):
    subject: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]
    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None
    ttl: int | None = Field(default=None, ge=60)

    @model_validator(mode="after")
    def _validate_resource(self) -> S3MintRequest:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3VerifyRequest(BaseModel):
    token: str = Field(min_length=1)
    audience: str | None = None


class S3EnforceRequest(BaseModel):
    token: str = Field(min_length=1)
    audience: str | None = None
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]


app = FastAPI(title="RAJA Control Plane", version="0.2.0")


@app.get("/", response_class=HTMLResponse)
def admin_home() -> HTMLResponse:
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>RAJA Admin</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; }
      h1 { margin-bottom: 0.5rem; }
      section { margin: 1.5rem 0; }
      pre { background: #f6f8fa; padding: 1rem; border-radius: 8px; }
    </style>
  </head>
  <body>
    <h1>RAJA Admin</h1>
    <p>Live control-plane data from the API.</p>
    <section>
      <h2>Principals</h2>
      <pre id="principals">Loading...</pre>
    </section>
    <section>
      <h2>Policies</h2>
      <pre id="policies">Loading...</pre>
    </section>
    <section>
      <h2>Audit Log</h2>
      <pre id="audit">Loading...</pre>
    </section>
    <script>
      function buildUrl(endpoint) {
        const basePath = window.location.pathname.endsWith('/')
          ? window.location.pathname
          : `${window.location.pathname}/`;
        return new URL(endpoint, `${window.location.origin}${basePath}`);
      }
      async function load(endpoint, target) {
        try {
          const response = await fetch(buildUrl(endpoint));
          const data = await response.json();
          document.getElementById(target).textContent = JSON.stringify(data, null, 2);
        } catch (err) {
          const url = buildUrl(endpoint);
          document.getElementById(target).textContent = `${err} (${url})`;
        }
      }
      load('principals', 'principals');
      load('policies', 'policies');
      load('audit', 'audit');
    </script>
  </body>
</html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compile")
def compile_policies() -> dict[str, Any]:
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    avp = _avp_client()
    mappings_table = _mappings_table()
    principal_table = _principal_table()

    policies_response = avp.list_policies(policyStoreId=policy_store_id, maxResults=100)
    policies_compiled = 0
    principal_scopes: dict[str, set[str]] = {}

    for policy_item in policies_response.get("policies", []):
        policy_id = policy_item["policyId"]
        policy_response = avp.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
        definition = policy_response.get("definition", {})
        static_def = definition.get("static", {})
        cedar_statement = static_def.get("statement", "")
        if not cedar_statement:
            continue

        principal_scope_map = compile_policy(cedar_statement)

        for principal, scope_list in principal_scope_map.items():
            updated_at = int(time.time())
            mappings_table.put_item(
                Item={"policy_id": policy_id, "scopes": scope_list, "updated_at": updated_at}
            )
            principal_scopes.setdefault(principal, set()).update(scope_list)

        policies_compiled += 1

    for principal, scopes in principal_scopes.items():
        principal_table.put_item(
            Item={"principal": principal, "scopes": list(scopes), "updated_at": int(time.time())}
        )

    return {
        "message": "Policies compiled successfully",
        "policies_compiled": policies_compiled,
        "principals": len(principal_scopes),
    }


@app.post("/token")
def issue_token(request: TokenRequest) -> dict[str, Any]:
    table = _principal_table()
    response = table.get_item(Key={"principal": request.principal})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail=f"Principal not found: {request.principal}")

    scopes = item.get("scopes", [])
    token = create_token(
        subject=request.principal,
        scopes=scopes,
        ttl=TOKEN_TTL,
        secret=_get_jwt_secret(),
    )
    return {"token": token, "principal": request.principal, "scopes": scopes}


@app.get("/principals")
def list_principals(limit: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    table = _principal_table()
    scan_kwargs: dict[str, Any] = {}
    if limit:
        scan_kwargs["Limit"] = limit
    response = table.scan(**scan_kwargs)
    return {"principals": response.get("Items", [])}


@app.get("/policies")
def list_policies(include_statements: bool = False) -> dict[str, Any]:
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    avp = _avp_client()
    response = avp.list_policies(policyStoreId=policy_store_id, maxResults=100)
    policies = response.get("policies", [])
    if not include_statements:
        return {"policies": policies}

    detailed: list[dict[str, Any]] = []
    for policy in policies:
        policy_id = policy.get("policyId")
        if not policy_id:
            continue
        policy_response = avp.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
        detailed.append({"policyId": policy_id, "definition": policy_response.get("definition")})
    return {"policies": detailed}


@app.get("/audit")
def audit_log() -> dict[str, Any]:
    return {"entries": [], "message": "Audit log not configured"}


@app.get("/s3-harness/config")
def s3_harness_config() -> dict[str, Any]:
    secret = _get_harness_secret()
    return {
        "issuer": _harness_issuer(),
        "audience": _harness_audience(),
        "jwks": _jwks_for_secret(secret),
    }


@app.post("/s3-harness/mint")
def s3_harness_mint(request: S3MintRequest) -> dict[str, Any]:
    secret = _get_harness_secret()
    issued_at = int(time.time())
    ttl = request.ttl if request.ttl is not None else _harness_ttl()
    resource = S3Resource(bucket=request.bucket, key=request.key, prefix=request.prefix)
    payload = {
        "iss": _harness_issuer(),
        "sub": request.subject,
        "aud": request.audience,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "action": request.action,
        "s3": _s3_authority_payload(resource),
    }
    headers = {"kid": _secret_kid(secret), "typ": "RAJ"}
    token = jwt.encode(payload, secret, algorithm="HS256", headers=headers)
    return {"token": token, "claims": payload, "kid": headers["kid"]}


@app.post("/s3-harness/verify")
def s3_harness_verify(request: S3VerifyRequest) -> dict[str, Any]:
    secret = _get_harness_secret()
    audience = request.audience or _harness_audience()
    try:
        payload = jwt.decode(
            request.token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=_harness_issuer(),
        )
    except jwt.ExpiredSignatureError:
        return {"valid": False, "error": "token expired"}
    except jwt.InvalidTokenError as exc:
        return {"valid": False, "error": f"invalid token: {exc}"}

    return {
        "valid": True,
        "payload": payload,
        "header": jwt.get_unverified_header(request.token),
    }


@app.post("/s3-harness/enforce")
def s3_harness_enforce(request: S3EnforceRequest) -> dict[str, Any]:
    secret = _get_harness_secret()
    audience = request.audience or _harness_audience()
    try:
        payload = jwt.decode(
            request.token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=_harness_issuer(),
        )
    except jwt.ExpiredSignatureError:
        return {"allowed": False, "reason": "token expired", "failed_check": "token"}
    except jwt.InvalidTokenError as exc:
        return {
            "allowed": False,
            "reason": f"invalid token: {exc}",
            "failed_check": "token",
        }

    authority_action = payload.get("action")
    authority_resource = payload.get("s3", {})
    try:
        authority = S3Resource(
            bucket=authority_resource.get("bucket", ""),
            key=authority_resource.get("key"),
            prefix=authority_resource.get("prefix"),
        )
    except Exception as exc:
        return {"allowed": False, "reason": f"invalid authority: {exc}", "failed_check": "resource"}

    action_matches = authority_action == request.action
    resource_matches = _s3_resource_allows(authority, request.bucket, request.key)

    if action_matches and resource_matches:
        return {
            "allowed": True,
            "reason": "request is within authority",
            "request": {"bucket": request.bucket, "key": request.key, "action": request.action},
            "authority": {"action": authority_action, "s3": authority_resource},
        }

    failed_check = "action" if not action_matches else "resource"
    return {
        "allowed": False,
        "reason": "request is outside authority",
        "failed_check": failed_check,
        "request": {"bucket": request.bucket, "key": request.key, "action": request.action},
        "authority": {"action": authority_action, "s3": authority_resource},
    }
