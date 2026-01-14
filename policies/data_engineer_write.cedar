// Data engineer can write objects to the raw-data bucket
permit(
  principal == Raja::User::"bob",
  action == Raja::Action::"s3:PutObject",
  resource in Raja::S3Bucket::"raw-data"
);

// Data engineer can read objects from the raw-data bucket
permit(
  principal == Raja::User::"bob",
  action == Raja::Action::"s3:GetObject",
  resource in Raja::S3Bucket::"raw-data"
);

// Data engineer can list the raw-data bucket
permit(
  principal == Raja::User::"bob",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raw-data"
);
