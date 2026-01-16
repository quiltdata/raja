import base64

import jwt
import pytest

from .helpers import issue_rajee_token, issue_token, request_json, require_api_issuer


@pytest.mark.integration
def test_token_service_issues_token_for_known_principal():
    token, scopes = issue_token("alice")
    assert token
    expected = {"Document:doc123:read", "Document:doc123:write"}
    assert expected.issubset(set(scopes))


@pytest.mark.integration
def test_token_service_rejects_unknown_principal():
    status, body = request_json("POST", "/token", {"principal": "unknown-user"})
    assert status == 404
    assert body.get("error") or body.get("detail")


@pytest.mark.integration
def test_rajee_token_validates_against_jwks():
    token = issue_rajee_token()
    status, body = request_json("GET", "/.well-known/jwks.json")
    assert status == 200

    keys = body.get("keys", [])
    assert keys, "JWKS keys missing"
    jwks_key = keys[0].get("k")
    assert jwks_key, "JWKS key material missing"

    padding = "=" * (-len(jwks_key) % 4)
    secret = base64.urlsafe_b64decode(jwks_key + padding).decode("utf-8")

    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="raja-s3-proxy",
        issuer=require_api_issuer(),
    )
    assert payload.get("sub") == "alice"
    assert "grants" in payload
