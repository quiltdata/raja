// Data analyst can read objects from the analytics bucket
permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"analytics-data/"
);

// Data analyst can list the analytics bucket
permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"analytics-data"
);
