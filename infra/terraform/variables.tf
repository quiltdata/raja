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
  description = "Environment tag and Cedar template value for {{env}} substitutions."
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

variable "taj_cache_ttl_seconds" {
  description = "TTL (seconds) for cached TAJ decisions in the RALE authorizer table."
  type        = number
  default     = 300
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
