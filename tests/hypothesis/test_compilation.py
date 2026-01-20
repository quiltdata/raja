import pytest

from raja import compile_policies, create_token, decode_token


@pytest.mark.hypothesis
def test_compilation_token_scopes_match_policy():
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        )
    ]
    compiled = compile_policies(policies)
    scopes = compiled.get("alice", [])

    token = create_token("alice", scopes, ttl=300, secret="secret")
    payload = decode_token(token)
    assert payload["scopes"] == scopes
