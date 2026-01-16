"""Generate test JWTs with grants for local testing."""

import sys
from datetime import datetime, timedelta, timezone

import jwt

TEST_SECRET = "test-secret-key-for-local-testing"


def generate_token(grants: list[str], ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": "User::test-user",
        "iss": "https://test.local",
        "aud": ["raja-s3-proxy"],
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "grants": grants,
    }
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_test_token.py <grant1> [grant2] ...")
        print("Example: python generate_test_token.py 's3:GetObject/bucket/uploads/'")
        sys.exit(1)

    grants = sys.argv[1:]
    token = generate_token(grants)

    print(f"Generated token with grants: {grants}")
    print("\nToken (valid for 1 hour):")
    print(token)
    print("\nTest with curl:")
    print(f'curl -H "Authorization: Bearer {token}" http://localhost:10000/bucket/key.txt')
