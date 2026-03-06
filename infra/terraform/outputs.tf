output "api_url" {
  description = "Base API URL for the RAJA control plane."
  value       = "${aws_api_gateway_stage.prod.invoke_url}/"
}

output "policy_store_id" {
  description = "Amazon Verified Permissions policy store ID."
  value       = aws_verifiedpermissions_policy_store.raja.policy_store_id
}

output "policy_store_arn" {
  description = "Amazon Verified Permissions policy store ARN."
  value       = aws_verifiedpermissions_policy_store.raja.arn
}

output "control_plane_lambda_arn" {
  description = "Control plane Lambda ARN."
  value       = aws_lambda_function.control_plane.arn
}

output "mappings_table_name" {
  description = "Policy scope mappings DynamoDB table."
  value       = aws_dynamodb_table.policy_scope_mappings.name
}

output "principal_table_name" {
  description = "Principal scopes DynamoDB table."
  value       = aws_dynamodb_table.principal_scopes.name
}

output "audit_table_name" {
  description = "Audit log DynamoDB table."
  value       = aws_dynamodb_table.audit_log.name
}

output "manifest_cache_table_name" {
  description = "Manifest cache table for RALE router."
  value       = aws_dynamodb_table.manifest_cache.name
}

output "taj_cache_table_name" {
  description = "TAJ decision cache table for RALE authorizer."
  value       = aws_dynamodb_table.taj_cache.name
}

output "jwt_secret_arn" {
  description = "JWT signing secret ARN."
  value       = aws_secretsmanager_secret.jwt.arn
}

output "harness_secret_arn" {
  description = "Harness signing secret ARN."
  value       = aws_secretsmanager_secret.harness.arn
}

output "rajee_endpoint" {
  description = "Base URL for the RAJEE Envoy S3 proxy."
  value       = "${local.rajee_endpoint_protocol}://${aws_lb.rajee.dns_name}"
}

output "rajee_test_bucket_name" {
  description = "S3 bucket used for RAJEE integration tests."
  value       = aws_s3_bucket.rajee_test.bucket
}

output "envoy_repository_uri" {
  description = "ECR repository URI for Envoy images."
  value       = aws_ecr_repository.envoy.repository_url
}

output "envoy_image_tag" {
  description = "Envoy image tag deployed by ECS."
  value       = local.envoy_image_tag_effective
}

output "rajee_admin_url" {
  description = "Envoy admin UI URL (only set when admin_allowed_cidrs is non-empty)."
  value       = length(var.admin_allowed_cidrs) > 0 ? "http://${aws_lb.rajee.dns_name}:9901/" : ""
}

output "rale_authorizer_arn" {
  description = "RALE authorizer Lambda ARN."
  value       = aws_lambda_function.rale_authorizer.arn
}

output "rale_authorizer_url" {
  description = "RALE authorizer Lambda Function URL."
  value       = aws_lambda_function_url.rale_authorizer.function_url
}

output "rale_router_arn" {
  description = "RALE router Lambda ARN."
  value       = aws_lambda_function.rale_router.arn
}

output "rale_router_url" {
  description = "RALE router Lambda Function URL."
  value       = aws_lambda_function_url.rale_router.function_url
}

output "legacy_cdk_outputs" {
  description = "CDK-compatible output shape for existing scripts/tests."
  value = {
    RajaAvpStack = {
      PolicyStoreId  = aws_verifiedpermissions_policy_store.raja.policy_store_id
      PolicyStoreArn = aws_verifiedpermissions_policy_store.raja.arn
    }
    RajaServicesStack = {
      ApiUrl                = "${aws_api_gateway_stage.prod.invoke_url}/"
      PolicyStoreId         = aws_verifiedpermissions_policy_store.raja.policy_store_id
      PolicyStoreArn        = aws_verifiedpermissions_policy_store.raja.arn
      ControlPlaneLambdaArn = aws_lambda_function.control_plane.arn
      MappingsTableName     = aws_dynamodb_table.policy_scope_mappings.name
      PrincipalTableName    = aws_dynamodb_table.principal_scopes.name
      AuditTableName        = aws_dynamodb_table.audit_log.name
      JWTSecretArn          = aws_secretsmanager_secret.jwt.arn
      HarnessSecretArn      = aws_secretsmanager_secret.harness.arn
      RaleAuthorizerArn     = aws_lambda_function.rale_authorizer.arn
      RaleRouterArn         = aws_lambda_function.rale_router.arn
      RaleAuthorizerUrl     = aws_lambda_function_url.rale_authorizer.function_url
      RaleRouterUrl         = aws_lambda_function_url.rale_router.function_url
      ManifestCacheTable    = aws_dynamodb_table.manifest_cache.name
      TajCacheTable         = aws_dynamodb_table.taj_cache.name
    }
    RajeeEnvoyStack = {
      DeploymentPlatform                  = lookup(local.platform_labels, var.ecs_cpu_architecture, var.ecs_cpu_architecture)
      TestBucketName                      = aws_s3_bucket.rajee_test.bucket
      RajeeEndpoint                       = "${local.rajee_endpoint_protocol}://${aws_lb.rajee.dns_name}"
      EnvoyRepositoryUri                  = aws_ecr_repository.envoy.repository_url
      RajeeServiceLoadBalancerDNSC2103F5C = aws_lb.rajee.dns_name
      RajeeServiceServiceURL78DE2D42      = "${local.rajee_endpoint_protocol}://${aws_lb.rajee.dns_name}"
    }
  }
}
