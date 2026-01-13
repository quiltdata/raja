from raja.compiler import compile_policies, compile_policy


def test_compile_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"read", '
        'resource == Document::"doc1");'
    )
    compiled = compile_policy(policy)
    assert compiled == {"alice": ["Document:doc1:read"]}


def test_compile_policy_forbid_ignored():
    policy = (
        'forbid(principal == User::"alice", action == Action::"read", '
        'resource == Document::"doc1");'
    )
    compiled = compile_policy(policy)
    assert compiled == {}


def test_compile_policies_aggregates():
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"read", '
            'resource == Document::"doc1");'
        ),
        (
            'permit(principal == User::"alice", action == Action::"write", '
            'resource == Document::"doc1");'
        ),
    ]
    compiled = compile_policies(policies)
    assert compiled == {"alice": ["Document:doc1:read", "Document:doc1:write"]}
