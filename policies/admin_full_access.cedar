// Admin has full access to all S3 buckets and objects
permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"*"
);

permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:PutObject",
  resource == Raja::S3Object::"*"
);

permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:DeleteObject",
  resource == Raja::S3Object::"*"
);

permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"*"
);

permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:GetBucketLocation",
  resource == Raja::S3Bucket::"*"
);

permit(
  principal == Raja::User::"admin",
  action == Raja::Action::"s3:DeleteBucket",
  resource == Raja::S3Bucket::"*"
);
