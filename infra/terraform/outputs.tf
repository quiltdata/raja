output "api_url" {
  description = "Base API URL for the RAJA control plane."
  value       = "${aws_api_gateway_stage.prod.invoke_url}/"
}

output "datazone_domain_id" {
  description = "Amazon DataZone domain identifier."
  value       = aws_datazone_domain.raja.id
}

output "datazone_portal_url" {
  description = "Amazon DataZone portal URL."
  value       = aws_datazone_domain.raja.portal_url
}

output "datazone_owner_project_id" {
  description = "Amazon DataZone owner project identifier for RAJA package listings."
  value       = aws_datazone_project.owner.id
}

output "datazone_users_project_id" {
  description = "Amazon DataZone project identifier for standard user principals."
  value       = aws_datazone_project.users.id
}

output "datazone_guests_project_id" {
  description = "Amazon DataZone project identifier for guest principals."
  value       = aws_datazone_project.guests.id
}

output "datazone_owner_environment_id" {
  description = "Amazon DataZone environment identifier for the owner project."
  value       = var.datazone_owner_environment_id
}

output "datazone_users_environment_id" {
  description = "Amazon DataZone environment identifier for the users project."
  value       = var.datazone_users_environment_id
}

output "datazone_guests_environment_id" {
  description = "Amazon DataZone environment identifier for the guests project."
  value       = var.datazone_guests_environment_id
}

output "datazone_owner_environment_role_arn" {
  description = "IAM role ARN used by the owner DataZone environment."
  value       = aws_iam_role.datazone_environment_owner.arn
}

output "datazone_users_environment_role_arn" {
  description = "IAM role ARN used by the users DataZone environment."
  value       = aws_iam_role.datazone_environment_users.arn
}

output "datazone_guests_environment_role_arn" {
  description = "IAM role ARN used by the guests DataZone environment."
  value       = aws_iam_role.datazone_environment_guests.arn
}

output "datazone_package_asset_type" {
  description = "Amazon DataZone asset type name for RAJA package listings."
  value       = aws_datazone_asset_type.quilt_package.name
}

output "datazone_package_asset_type_revision" {
  description = "Amazon DataZone asset type revision for RAJA package listings."
  value       = aws_datazone_asset_type.quilt_package.revision
}

output "control_plane_lambda_arn" {
  description = "Control plane Lambda ARN."
  value       = aws_lambda_function.control_plane.arn
}


output "jwt_secret_arn" {
  description = "JWT signing secret ARN."
  value       = aws_secretsmanager_secret.jwt.arn
}

output "rajee_endpoint" {
  description = "Base URL for the RAJEE Envoy S3 proxy."
  value       = "${local.rajee_endpoint_protocol}://${aws_lb.rajee.dns_name}"
}

output "rajee_test_bucket_name" {
  description = "S3 bucket used for RAJEE integration tests."
  value       = aws_s3_bucket.rajee_test.bucket
}

output "rajee_registry_bucket_name" {
  description = "S3 bucket used as the Quilt package registry."
  value       = aws_s3_bucket.rajee_registry.bucket
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
