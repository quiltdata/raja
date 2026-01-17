from raja.compiler import compile_policies, compile_policy


def test_compile_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv" in S3Bucket::"analytics-data");'
    )
    compiled = compile_policy(policy)
    assert compiled == {"alice": ["S3Object:analytics-data/report.csv:s3:GetObject"]}


def test_compile_policy_forbid_ignored():
    policy = (
        'forbid(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv" in S3Bucket::"analytics-data");'
    )
    compiled = compile_policy(policy)
    assert compiled == {}


def test_compile_policies_aggregates():
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv" in S3Bucket::"analytics-data");'
        ),
        (
            'permit(principal == User::"alice", action == Action::"s3:PutObject", '
            'resource == S3Object::"upload.csv" in S3Bucket::"raw-data");'
        ),
    ]
    compiled = compile_policies(policies)
    assert compiled == {
        "alice": [
            "S3Object:analytics-data/report.csv:s3:GetObject",
            "S3Object:raw-data/upload.csv:s3:PutObject",
        ]
    }
