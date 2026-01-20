"""Generate test JWTs with grants for local testing."""

import sys

# Add tests/shared to path to use TokenBuilder
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from token_builder import TokenBuilder

TEST_SECRET = "test-secret-key-for-local-testing"


def generate_token(grants: list[str], ttl_seconds: int = 3600) -> str:
    """Generate test token using shared TokenBuilder."""
    return (
        TokenBuilder(secret=TEST_SECRET, issuer="https://test.local", audience=["raja-s3-proxy"])
        .with_subject("User::test-user")
        .with_ttl(ttl_seconds)
        .with_grants(grants)
        .build()
    )


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
