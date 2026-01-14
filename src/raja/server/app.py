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
      @import url("https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@400;500;600;700&display=swap");
      :root {
        color-scheme: light;
        --bg: #f7f2e6;
        --bg-strong: #f2e7d2;
        --ink: #1d1b16;
        --muted: #5a5345;
        --accent: #ef6a44;
        --accent-2: #3c7a6f;
        --card: #fff8ee;
        --border: #e7d7bf;
        --shadow: rgba(48, 37, 20, 0.12);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Space Grotesk", "Segoe UI", sans-serif;
        color: var(--ink);
        background: radial-gradient(circle at top, #fff4db 0%, var(--bg) 45%, #efe1c3 100%);
        min-height: 100vh;
      }
      body::before {
        content: "";
        position: fixed;
        inset: 0;
        background: linear-gradient(120deg, rgba(239, 106, 68, 0.15), rgba(60, 122, 111, 0.1));
        pointer-events: none;
        opacity: 0.7;
      }
      header {
        padding: 3rem 7vw 1.5rem;
        position: relative;
        z-index: 1;
      }
      header h1 {
        margin: 0 0 0.5rem;
        font-size: clamp(2.5rem, 3vw, 3.5rem);
        letter-spacing: -0.02em;
      }
      header p {
        margin: 0;
        color: var(--muted);
        max-width: 620px;
        font-size: 1.05rem;
      }
      main {
        padding: 0 7vw 4rem;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 1.5rem;
        position: relative;
        z-index: 1;
      }
      .card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 1.5rem;
        box-shadow: 0 20px 40px var(--shadow);
        display: flex;
        flex-direction: column;
        gap: 1rem;
        animation: rise 0.6s ease forwards;
        opacity: 0;
        transform: translateY(10px);
        animation-delay: var(--delay, 0s);
      }
      .card h2 {
        margin: 0;
        font-size: 1.35rem;
      }
      .card p {
        margin: 0;
        color: var(--muted);
        font-size: 0.95rem;
      }
      .pill {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.3rem 0.7rem;
        border-radius: 999px;
        background: rgba(239, 106, 68, 0.1);
        color: var(--accent);
        font-weight: 600;
        font-size: 0.8rem;
        letter-spacing: 0.02em;
      }
      label {
        font-size: 0.85rem;
        color: var(--muted);
        display: block;
        margin-bottom: 0.3rem;
      }
      input, select, textarea, button {
        font-family: "Space Grotesk", "Segoe UI", sans-serif;
        font-size: 0.95rem;
      }
      input, select, textarea {
        width: 100%;
        border-radius: 12px;
        border: 1px solid var(--border);
        padding: 0.6rem 0.8rem;
        background: #fffdfa;
      }
      textarea {
        min-height: 110px;
        resize: vertical;
        font-family: "JetBrains Mono", monospace;
        font-size: 0.85rem;
      }
      .field-row {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 0.8rem;
      }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 0.6rem;
      }
      button {
        border: none;
        border-radius: 999px;
        padding: 0.6rem 1.2rem;
        background: var(--accent);
        color: #fffaf2;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      button.secondary {
        background: #f1e3cb;
        color: var(--ink);
      }
      button:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 16px rgba(239, 106, 68, 0.25);
      }
      pre {
        background: #fffdfa;
        border-radius: 12px;
        border: 1px dashed var(--border);
        padding: 0.8rem;
        font-family: "JetBrains Mono", monospace;
        font-size: 0.8rem;
        overflow-x: auto;
        white-space: pre-wrap;
      }
      .status {
        font-weight: 600;
        padding: 0.2rem 0.5rem;
        border-radius: 6px;
        background: rgba(60, 122, 111, 0.12);
        color: var(--accent-2);
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
      }
      .status.deny {
        background: rgba(239, 106, 68, 0.16);
        color: var(--accent);
      }
      .control-plane {
        border-style: dashed;
        border-color: rgba(60, 122, 111, 0.3);
      }
      @keyframes rise {
        to { opacity: 1; transform: translateY(0); }
      }
      @media (max-width: 720px) {
        header {
          padding: 2rem 6vw 1rem;
        }
        main {
          padding: 0 6vw 3rem;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .card {
          animation: none;
          opacity: 1;
          transform: none;
        }
      }
    </style>
  </head>
  <body>
    <header>
      <div class="pill">S3 Validation Harness</div>
      <h1>RAJA Admin</h1>
      <p>Mint, verify, and enforce Resource Authorization JWTs for S3 without policy
      evaluation or AWS calls.</p>
    </header>
    <main>
      <section class="card" style="--delay: 0s;">
        <h2>Issuer &amp; JWKS</h2>
        <p>Live signing metadata for validation and key introspection.</p>
        <div>
          <label>Issuer</label>
          <div class="status" id="issuer">Loading...</div>
        </div>
        <div>
          <label>Audience</label>
          <div class="status" id="audience">Loading...</div>
        </div>
        <div>
          <label>JWKS</label>
          <pre id="jwks">Loading...</pre>
        </div>
        <div class="actions">
          <button class="secondary" id="refresh-config">Refresh metadata</button>
        </div>
      </section>
      <section class="card" style="--delay: 0.05s;">
        <h2>Mint RAJ</h2>
        <p>Create a short-lived authorization for a single S3 action and resource.</p>
        <form id="mint-form">
          <div class="field-row">
            <div>
              <label for="mint-subject">Subject (sub)</label>
              <input id="mint-subject" name="subject" placeholder="User::alice" />
            </div>
            <div>
              <label for="mint-audience">Audience (aud)</label>
              <input id="mint-audience" name="audience" placeholder="raja-s3" />
            </div>
          </div>
          <div class="field-row">
            <div>
              <label for="mint-bucket">Bucket</label>
              <input id="mint-bucket" name="bucket" placeholder="my-bucket" />
            </div>
            <div>
              <label for="mint-action">Action</label>
              <select id="mint-action" name="action">
                <option value="s3:GetObject">s3:GetObject</option>
                <option value="s3:PutObject">s3:PutObject</option>
                <option value="s3:ListBucket">s3:ListBucket</option>
                <option value="s3:ListBucketMultipartUploads">s3:ListBucketMultipartUploads</option>
                <option value="s3:ListMultipartUploadParts">s3:ListMultipartUploadParts</option>
              </select>
            </div>
          </div>
          <div class="field-row">
            <div>
              <label for="mint-key">Key (exact)</label>
              <input id="mint-key" name="key" placeholder="reports/2024/summary.csv" />
            </div>
            <div>
              <label for="mint-prefix">Prefix (starts with)</label>
              <input id="mint-prefix" name="prefix" placeholder="reports/2024/" />
            </div>
          </div>
          <div class="field-row">
            <div>
              <label for="mint-ttl">TTL (seconds)</label>
              <input id="mint-ttl" name="ttl" type="number" min="60" placeholder="300" />
            </div>
          </div>
          <div class="actions">
            <button type="submit">Mint RAJ</button>
          </div>
        </form>
        <div>
          <label>Token</label>
          <textarea id="mint-token" readonly placeholder="Your RAJ will appear here."></textarea>
        </div>
        <div>
          <label>Claims</label>
          <pre id="mint-claims">Waiting for mint.</pre>
        </div>
      </section>
      <section class="card" style="--delay: 0.1s;">
        <h2>Verify RAJ</h2>
        <p>Confirm signature, issuer, audience, and expiration.</p>
        <form id="verify-form">
          <div>
            <label for="verify-token">Token</label>
            <textarea id="verify-token" name="token"
              placeholder="Paste a RAJ to verify."></textarea>
          </div>
          <div>
            <label for="verify-aud">Audience (aud)</label>
            <input id="verify-aud" name="audience" placeholder="raja-s3" />
          </div>
          <div class="actions">
            <button type="submit">Verify token</button>
          </div>
        </form>
        <div>
          <label>Verification result</label>
          <pre id="verify-output">Waiting for token.</pre>
        </div>
      </section>
      <section class="card" style="--delay: 0.15s;">
        <h2>Simulate Enforcement</h2>
        <p>Check Request ⊆ Authority and see why it fails.</p>
        <form id="enforce-form">
          <div>
            <label for="enforce-token">Token</label>
            <textarea id="enforce-token" name="token"
              placeholder="Paste or use the minted RAJ."></textarea>
          </div>
          <div class="field-row">
            <div>
              <label for="enforce-aud">Audience (aud)</label>
              <input id="enforce-aud" name="audience" placeholder="raja-s3" />
            </div>
            <div>
              <label for="enforce-action">Action</label>
              <select id="enforce-action" name="action">
                <option value="s3:GetObject">s3:GetObject</option>
                <option value="s3:PutObject">s3:PutObject</option>
                <option value="s3:ListBucket">s3:ListBucket</option>
                <option value="s3:ListBucketMultipartUploads">s3:ListBucketMultipartUploads</option>
                <option value="s3:ListMultipartUploadParts">s3:ListMultipartUploadParts</option>
              </select>
            </div>
          </div>
          <div class="field-row">
            <div>
              <label for="enforce-bucket">Bucket</label>
              <input id="enforce-bucket" name="bucket" placeholder="my-bucket" />
            </div>
            <div>
              <label for="enforce-key">Key</label>
              <input id="enforce-key" name="key" placeholder="reports/2024/summary.csv" />
            </div>
          </div>
          <div class="actions">
            <button type="submit">Simulate decision</button>
          </div>
        </form>
        <div>
          <label>Decision</label>
          <pre id="enforce-output">Waiting for request.</pre>
        </div>
      </section>
      <section class="card control-plane" style="--delay: 0.2s;">
        <h2>Control Plane (Optional)</h2>
        <p>Fetches live AWS control-plane data only when requested.</p>
        <div class="actions">
          <button class="secondary" id="load-control-plane">Load control-plane data</button>
        </div>
        <div>
          <label>Principals</label>
          <pre id="principals">Not loaded.</pre>
        </div>
        <div>
          <label>Policies</label>
          <pre id="policies">Not loaded.</pre>
        </div>
        <div>
          <label>Audit Log</label>
          <pre id="audit">Not loaded.</pre>
        </div>
      </section>
    </main>
    <script>
      const select = (id) => document.getElementById(id);
      function buildUrl(endpoint) {
        const basePath = window.location.pathname.endsWith("/")
          ? window.location.pathname
          : `${window.location.pathname}/`;
        return new URL(endpoint, `${window.location.origin}${basePath}`);
      }
      function writeJson(id, data) {
        select(id).textContent = JSON.stringify(data, null, 2);
      }
      function setInputDefault(id, value) {
        const el = select(id);
        if (!el.value) {
          el.value = value;
        }
      }
      async function refreshConfig() {
        try {
          const response = await fetch(buildUrl("s3-harness/config"));
          const data = await response.json();
          select("issuer").textContent = data.issuer;
          select("audience").textContent = data.audience;
          writeJson("jwks", data.jwks);
          setInputDefault("mint-audience", data.audience);
          setInputDefault("verify-aud", data.audience);
          setInputDefault("enforce-aud", data.audience);
        } catch (err) {
          select("issuer").textContent = "Unavailable";
          select("audience").textContent = "Unavailable";
          select("jwks").textContent = String(err);
        }
      }
      async function postJson(endpoint, payload) {
        const response = await fetch(buildUrl(endpoint), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        return { ok: response.ok, data };
      }
      select("refresh-config").addEventListener("click", refreshConfig);
      select("mint-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = {
          subject: select("mint-subject").value.trim() || "User::demo",
          audience: select("mint-audience").value.trim() || "raja-s3",
          action: select("mint-action").value,
          bucket: select("mint-bucket").value.trim() || "demo-bucket",
        };
        const key = select("mint-key").value.trim();
        const prefix = select("mint-prefix").value.trim();
        if (key && prefix) {
          writeJson("mint-claims", { error: "Provide a key OR prefix, not both." });
          return;
        }
        if (!key && !prefix) {
          writeJson("mint-claims", { error: "Provide a key or prefix." });
          return;
        }
        if (key) {
          payload.key = key;
        } else {
          payload.prefix = prefix;
        }
        const ttl = Number(select("mint-ttl").value);
        if (ttl) {
          payload.ttl = ttl;
        }
        const result = await postJson("s3-harness/mint", payload);
        if (!result.ok) {
          writeJson("mint-claims", result.data);
          return;
        }
        select("mint-token").value = result.data.token;
        select("verify-token").value = result.data.token;
        select("enforce-token").value = result.data.token;
        writeJson("mint-claims", result.data);
      });
      select("verify-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = {
          token: select("verify-token").value.trim(),
          audience: select("verify-aud").value.trim() || undefined,
        };
        const result = await postJson("s3-harness/verify", payload);
        writeJson("verify-output", result.data);
      });
      select("enforce-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = {
          token: select("enforce-token").value.trim(),
          audience: select("enforce-aud").value.trim() || undefined,
          action: select("enforce-action").value,
          bucket: select("enforce-bucket").value.trim(),
          key: select("enforce-key").value.trim(),
        };
        const result = await postJson("s3-harness/enforce", payload);
        writeJson("enforce-output", result.data);
      });
      select("load-control-plane").addEventListener("click", async () => {
        const targets = [
          { endpoint: "principals", id: "principals" },
          { endpoint: "policies", id: "policies" },
          { endpoint: "audit", id: "audit" },
        ];
        for (const target of targets) {
          try {
            const response = await fetch(buildUrl(target.endpoint));
            const data = await response.json();
            writeJson(target.id, data);
          } catch (err) {
            select(target.id).textContent = String(err);
          }
        }
      });
      refreshConfig();
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
