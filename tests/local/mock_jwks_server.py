"""Mock JWKS server for local testing."""

import base64
from typing import Any

from fastapi import FastAPI
from uvicorn import run

app = FastAPI()

TEST_SECRET = "test-secret-key-for-local-testing"


@app.get("/.well-known/jwks.json")
def get_jwks() -> dict[str, Any]:
    key_bytes = TEST_SECRET.encode("utf-8")
    k_value = base64.urlsafe_b64encode(key_bytes).decode("utf-8").rstrip("=")
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": "test-key-1",
                "alg": "HS256",
                "k": k_value,
            }
        ]
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    print("Starting mock JWKS server on http://localhost:8001")
    print("JWKS endpoint: http://localhost:8001/.well-known/jwks.json")
    run(app, host="0.0.0.0", port=8001)
