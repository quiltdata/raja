from __future__ import annotations

import os
import time
from typing import Any

import boto3  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from raja import compile_policy, create_token

JWT_SECRET_ARN = os.environ.get("JWT_SECRET_ARN")
POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID")
MAPPINGS_TABLE = os.environ.get("MAPPINGS_TABLE")
PRINCIPAL_TABLE = os.environ.get("PRINCIPAL_TABLE")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))

_jwt_secret_cache: str | None = None


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


class TokenRequest(BaseModel):
    principal: str


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
