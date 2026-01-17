// @description Grant test-user access to rajee-integration/ prefix in test buckets
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_auth_with_real_scopes
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow PutObject operations (includes multipart uploads in enforcement)
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_auth_with_real_scopes
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:PutObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow delete of objects within rajee-integration/ prefix
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_auth_with_real_scopes
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:DeleteObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow bucket listing for test buckets
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_list_bucket
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow list bucket versions for versioned buckets
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_versioning_operations
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:ListBucketVersions",
  resource == Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow read of object versions within rajee-integration/ prefix
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_versioning_operations
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObjectVersion",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);

// @description Allow delete of object versions within rajee-integration/ prefix
// @test tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_versioning_operations
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:DeleteObjectVersion",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);
