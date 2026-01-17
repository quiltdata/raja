// RAJEE Integration Test Policy
// Grants full access to the test prefix for integration testing

permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"raja-poc-test-712023778557-us-east-1/rajee-integration/"
);

permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:PutObject",
  resource == Raja::S3Object::"raja-poc-test-712023778557-us-east-1/rajee-integration/"
);

permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:DeleteObject",
  resource == Raja::S3Object::"raja-poc-test-712023778557-us-east-1/rajee-integration/"
);

permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raja-poc-test-712023778557-us-east-1"
);
