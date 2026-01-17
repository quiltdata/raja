// Principals
entity User {}
entity Role {}

// S3 Resources
// S3Object resources should be expressed as:
//   resource == Raja::S3Object::"key-or-prefix/" in Raja::S3Bucket::"bucket-or-prefix-"
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

action "s3:HeadObject" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:InitiateMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:UploadPart" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:CompleteMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:AbortMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:GetObjectVersion" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:PutObjectVersionTagging" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:GetObjectVersionTagging" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:DeleteObjectVersion" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:ListBucket" appliesTo {
  principal: [User, Role],
  resource: [S3Bucket]
}

action "s3:ListBucketVersions" appliesTo {
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
