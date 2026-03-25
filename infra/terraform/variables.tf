variable "aws_region" {
  description = "AWS region for the standalone RAJA stack."
  type        = string
}

variable "stack_name" {
  description = "Prefix used for Terraform resource names."
  type        = string
  default     = "raja-standalone"
}

variable "environment" {
  description = "Environment tag for stack naming and operational labeling."
  type        = string
  default     = "dev"
}

variable "python_bin" {
  description = "Python executable used to build Lambda artifacts."
  type        = string
  default     = "python3"
}

variable "token_ttl" {
  description = "Default token TTL (seconds) exposed to the control plane Lambda."
  type        = number
  default     = 3600
}

variable "raja_admin_key" {
  description = "Bearer token required for protected RAJA admin/control-plane endpoints."
  type        = string
  sensitive   = true
}

variable "raja_default_principal_username" {
  description = "Optional IAM username used to pre-populate the admin UI with the first configured principal."
  type        = string
  default     = ""
}

variable "rale_storage" {
  description = "Storage scheme used when constructing quilt URIs for RALE package manifests."
  type        = string
  default     = "s3"
}

variable "lambda_architecture" {
  description = "Lambda architecture for control plane and layer."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be one of: arm64, x86_64."
  }
}

variable "envoy_image_tag" {
  description = "Optional Envoy image tag in ECR. Leave empty to auto-build and use a content-hash tag."
  type        = string
  default     = ""
}

variable "build_envoy_image" {
  description = "Build and push the Envoy image during terraform apply if the tag is missing in ECR."
  type        = bool
  default     = true
}

variable "auth_disabled" {
  description = "Disable authorization checks in Envoy (fail-open bootstrap mode)."
  type        = bool
  default     = false
}

variable "use_public_grants" {
  description = "Enable public grants bypass for the RAJEE integration test prefix."
  type        = bool
  default     = false
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN. Set to enable HTTPS listener on port 443."
  type        = string
  default     = ""
}

variable "ecs_cpu_architecture" {
  description = "ECS/Fargate CPU architecture for RAJEE service."
  type        = string
  default     = "ARM64"

  validation {
    condition     = contains(["ARM64", "X86_64"], var.ecs_cpu_architecture)
    error_message = "ecs_cpu_architecture must be one of: ARM64, X86_64."
  }
}

variable "rajee_task_cpu" {
  description = "Task CPU units for the RAJEE ECS task definition."
  type        = number
  default     = 256
}

variable "rajee_task_memory" {
  description = "Task memory (MiB) for the RAJEE ECS task definition."
  type        = number
  default     = 512
}

variable "admin_allowed_cidrs" {
  description = "CIDRs allowed to access the Envoy admin UI on port 9901. Empty list disables public admin exposure."
  type        = list(string)
  default     = []
}

variable "registry_accessor_arns" {
  description = "IAM principal ARNs (users, roles) granted read/write access to the RAJEE registry bucket. Add Quilt platform roles or developer users here."
  type        = list(string)
  default     = []
}

variable "iceberg_s3_bucket" {
  description = "S3 bucket containing the Quilt Iceberg tables (without s3:// prefix)."
  type        = string
  default     = ""
}

variable "datazone_domain_name" {
  description = "Amazon DataZone domain name for the RAJA package-grant POC."
  type        = string
  default     = "raja-poc"
}

variable "datazone_owner_project_name" {
  description = "Amazon DataZone project that owns RAJA package assets."
  type        = string
  default     = "raja-owner"
}

variable "datazone_users_project_name" {
  description = "Amazon DataZone project for standard user principals."
  type        = string
  default     = "raja-users"
}

variable "datazone_guests_project_name" {
  description = "Amazon DataZone project for guest (read-only public) principals."
  type        = string
  default     = "raja-guests"
}

variable "perf_test_bucket" {
  description = "External S3 bucket used for performance benchmarks (e.g. data-yaml-spec-tests). Grants the RALE router Lambda read access and adds the bucket prefix to RAJEE_PUBLIC_PATH_PREFIXES so the auth-disabled Envoy baseline can reach it."
  type        = string
  default     = "data-yaml-spec-tests"
}

variable "datazone_package_asset_type" {
  description = "Custom Amazon DataZone asset type name used for Quilt package listings."
  type        = string
  default     = "QuiltPackage"
}

variable "datazone_projects" {
  description = "JSON blob mapping project keys to DataZone project_id/environment_id/project_label. Populated by sagemaker_gaps.py after environments are created and fed back in via TF_VAR_datazone_projects on subsequent runs."
  type        = string
  default     = ""
}
