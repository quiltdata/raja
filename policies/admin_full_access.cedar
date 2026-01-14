// Admin has full access to all S3 buckets and objects
permit(
  principal == Raja::User::"admin",
  action in [
    Raja::Action::"s3:GetObject",
    Raja::Action::"s3:PutObject",
    Raja::Action::"s3:DeleteObject",
    Raja::Action::"s3:ListBucket",
    Raja::Action::"s3:GetBucketLocation",
    Raja::Action::"s3:DeleteBucket"
  ],
  resource
);
