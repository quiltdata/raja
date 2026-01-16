// RAJEE Integration Test Policy
// Grants Alice access to the rajee-integration/ prefix in test buckets.

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"raja-poc-test-*/rajee-integration/*"
);

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:PutObject",
  resource == Raja::S3Object::"raja-poc-test-*/rajee-integration/*"
);

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:DeleteObject",
  resource == Raja::S3Object::"raja-poc-test-*/rajee-integration/*"
);

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raja-poc-test-*"
);
