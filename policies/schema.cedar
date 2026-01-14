// Principals
entity User {}
entity Role {}

// S3 Resources
entity S3Bucket {}
entity S3Object in [S3Bucket] {}

// S3 Actions
action "s3:GetObject" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:PutObject" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:DeleteObject" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:ListBucket" appliesTo {
  principal: [User, Role],
  resource: [S3Bucket]
}

action "s3:GetBucketLocation" appliesTo {
  principal: [User, Role],
  resource: [S3Bucket]
}

action "s3:DeleteBucket" appliesTo {
  principal: [User, Role],
  resource: [S3Bucket]
}
