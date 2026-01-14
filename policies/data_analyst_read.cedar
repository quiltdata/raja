// Data analyst can read objects from the analytics bucket
permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObject",
  resource in Raja::S3Bucket::"analytics-data"
);

// Data analyst can list the analytics bucket
permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"analytics-data"
);
