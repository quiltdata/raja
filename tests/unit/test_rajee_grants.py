from raja.rajee.grants import convert_scope_to_grant, convert_scopes_to_grants


def test_convert_scope_to_grant_s3_object():
    scope = "S3Object:analytics-data/report.csv:s3:GetObject"
    assert convert_scope_to_grant(scope) == "s3:GetObject/analytics-data/report.csv"


def test_convert_scope_to_grant_s3_bucket():
    scope = "S3Bucket:analytics-data:s3:ListBucket"
    assert convert_scope_to_grant(scope) == "s3:ListBucket/analytics-data/"


def test_convert_scope_to_grant_passthrough():
    scope = "s3:GetObject/analytics-data/report.csv"
    assert convert_scope_to_grant(scope) == scope


def test_convert_scopes_to_grants_filters_unknown():
    scopes = [
        "S3Object:analytics-data/report.csv:s3:GetObject",
        "Document:doc1:read",
    ]
    assert convert_scopes_to_grants(scopes) == ["s3:GetObject/analytics-data/report.csv"]
