from __future__ import annotations

from raja import AuthRequest, compile_policies, create_token, enforce


def main() -> None:
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"read", '
            'resource == Document::"doc1");'
        )
    ]
    compiled = compile_policies(policies)
    scopes = compiled.get("alice", [])

    secret = "local-dev-secret"
    token = create_token("alice", scopes, ttl=3600, secret=secret)

    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token, request, secret)

    print("Token scopes:", scopes)
    print("Decision:", decision.model_dump())


if __name__ == "__main__":
    main()
