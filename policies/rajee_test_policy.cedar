// RAJEE Integration Test Policy
// Grants full access to the test prefix for integration testing

permit (
    principal == User::"test-user",
    action in [
        Action::"s3:GetObject",
        Action::"s3:PutObject",
        Action::"s3:DeleteObject",
        Action::"s3:ListBucket",
        Action::"s3:GetObjectAttributes",
        Action::"s3:ListObjectVersions"
    ],
    resource
)
when {
    resource.bucket.startsWith("raja-poc-test") &&
    resource.key.startsWith("rajee-integration/")
};
