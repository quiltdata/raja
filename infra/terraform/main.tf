data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

locals {
  repo_root                    = abspath("${path.module}/../..")
  control_plane_source_dir     = "${local.repo_root}/lambda_handlers/control_plane"
  control_plane_requirements   = "${local.control_plane_source_dir}/requirements.txt"
  rale_authorizer_source_dir   = "${local.repo_root}/lambda_handlers/rale_authorizer"
  rale_authorizer_requirements = "${local.rale_authorizer_source_dir}/requirements.txt"
  rale_router_source_dir       = "${local.repo_root}/lambda_handlers/rale_router"
  rale_router_requirements     = "${local.rale_router_source_dir}/requirements.txt"
  raja_source_dir              = "${local.repo_root}/src/raja"
  layer_requirements           = "${local.repo_root}/infra/raja_poc/layers/raja/requirements.txt"
  build_dir                 = "${path.module}/build"
  control_plane_build_dir   = "${local.build_dir}/control_plane"
  rale_authorizer_build_dir = "${local.build_dir}/rale_authorizer"
  rale_router_build_dir     = "${local.build_dir}/rale_router"
  layer_build_dir           = "${local.build_dir}/raja_layer"

  layer_source_hash = sha256(join("", concat(
    [filesha256(local.layer_requirements)],
    [for source_file in fileset(local.raja_source_dir, "**") : filesha256("${local.raja_source_dir}/${source_file}") if !endswith(source_file, ".pyc")]
  )))

  control_plane_source_hash = sha256(join("", concat(
    [filesha256(local.control_plane_requirements)],
    [for source_file in fileset(local.control_plane_source_dir, "**") : filesha256("${local.control_plane_source_dir}/${source_file}") if !endswith(source_file, ".pyc")]
  )))
  rale_authorizer_source_hash = sha256(join("", concat(
    [filesha256(local.rale_authorizer_requirements)],
    [for source_file in fileset(local.rale_authorizer_source_dir, "**") : filesha256("${local.rale_authorizer_source_dir}/${source_file}") if !endswith(source_file, ".pyc")]
  )))
  rale_router_source_hash = sha256(join("", concat(
    [filesha256(local.rale_router_requirements)],
    [for source_file in fileset(local.rale_router_source_dir, "**") : filesha256("${local.rale_router_source_dir}/${source_file}") if !endswith(source_file, ".pyc")]
  )))
  lambda_pip_platform = var.lambda_architecture == "arm64" ? "aarch64-manylinux2014" : "x86_64-manylinux2014"

  envoy_source_dir = "${local.repo_root}/infra/raja_poc/assets/envoy"
  envoy_source_hash = sha256(join("", [
    for source_file in fileset(local.envoy_source_dir, "**") : filesha256("${local.envoy_source_dir}/${source_file}")
    if !endswith(source_file, ".pyc")
  ]))
  envoy_image_tag_effective = var.envoy_image_tag != "" ? var.envoy_image_tag : substr(local.envoy_source_hash, 0, 8)

  azs                      = slice(data.aws_availability_zones.available.names, 0, 2)
  rajee_endpoint_protocol  = var.certificate_arn == "" ? "http" : "https"
  rajee_public_path_prefix = "/${aws_s3_bucket.rajee_test.bucket}"

  rajee_public_grants = var.use_public_grants ? [
    "s3:GetObject/${aws_s3_bucket.rajee_test.bucket}/rajee-integration/",
    "s3:PutObject/${aws_s3_bucket.rajee_test.bucket}/rajee-integration/",
    "s3:DeleteObject/${aws_s3_bucket.rajee_test.bucket}/rajee-integration/",
    "s3:ListBucket/${aws_s3_bucket.rajee_test.bucket}/",
    "s3:GetObjectAttributes/${aws_s3_bucket.rajee_test.bucket}/rajee-integration/",
    "s3:ListObjectVersions/${aws_s3_bucket.rajee_test.bucket}/rajee-integration/",
  ] : []

  api_url       = trimsuffix(aws_api_gateway_stage.prod.invoke_url, "/")
  api_url_parts = split("/", trimprefix(trimprefix(local.api_url, "https://"), "http://"))
  api_host      = local.api_url_parts[0]
  api_scheme    = startswith(local.api_url, "https://") ? "https" : "http"
  jwks_endpoint = "${local.api_url}/.well-known/jwks.json"
  issuer        = "${local.api_scheme}://${local.api_host}"

  platform_labels = {
    ARM64  = "ARM64 (linux/arm64)"
    X86_64 = "X86_64 (linux/amd64)"
  }
  lambda_arn_prefix           = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function"
  control_plane_lambda_name   = "${var.stack_name}-control-plane"
  rale_authorizer_lambda_name = "${var.stack_name}-rale-authorizer"
  rale_router_lambda_name     = "${var.stack_name}-rale-router"
  control_plane_lambda_arn    = "${local.lambda_arn_prefix}:${local.control_plane_lambda_name}"
  rale_authorizer_lambda_arn  = "${local.lambda_arn_prefix}:${local.rale_authorizer_lambda_name}"
  rale_router_lambda_arn      = "${local.lambda_arn_prefix}:${local.rale_router_lambda_name}"
  datazone_domain_exec_role   = "${var.stack_name}-datazone-domain-execution"
}

resource "null_resource" "build_raja_layer" {
  triggers = {
    source_hash     = local.layer_source_hash
    lambda_platform = local.lambda_pip_platform
    lambda_arch     = var.lambda_architecture
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.layer_build_dir}"
      mkdir -p "${local.layer_build_dir}/python"
      uv pip install --no-cache \
        --python-platform "${local.lambda_pip_platform}" --python-version 3.12 --only-binary :all: \
        -r "${local.layer_requirements}" --target "${local.layer_build_dir}/python"
      cp -R "${local.raja_source_dir}" "${local.layer_build_dir}/python/raja"
    EOT
  }
}

resource "null_resource" "build_control_plane" {
  triggers = {
    source_hash     = local.control_plane_source_hash
    lambda_platform = local.lambda_pip_platform
    lambda_arch     = var.lambda_architecture
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.control_plane_build_dir}"
      mkdir -p "${local.control_plane_build_dir}"
      uv pip install --no-cache \
        --python-platform "${local.lambda_pip_platform}" --python-version 3.12 --only-binary :all: \
        -r "${local.control_plane_requirements}" --target "${local.control_plane_build_dir}"
      cp -R "${local.control_plane_source_dir}/." "${local.control_plane_build_dir}/"
    EOT
  }
}

resource "null_resource" "build_rale_authorizer" {
  triggers = {
    source_hash     = local.rale_authorizer_source_hash
    lambda_platform = local.lambda_pip_platform
    lambda_arch     = var.lambda_architecture
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.rale_authorizer_build_dir}"
      mkdir -p "${local.rale_authorizer_build_dir}"
      uv pip install --no-cache \
        --python-platform "${local.lambda_pip_platform}" --python-version 3.12 --only-binary :all: \
        -r "${local.rale_authorizer_requirements}" --target "${local.rale_authorizer_build_dir}"
      cp -R "${local.rale_authorizer_source_dir}/." "${local.rale_authorizer_build_dir}/"
    EOT
  }
}

resource "null_resource" "build_rale_router" {
  triggers = {
    source_hash     = local.rale_router_source_hash
    lambda_platform = local.lambda_pip_platform
    lambda_arch     = var.lambda_architecture
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.rale_router_build_dir}"
      mkdir -p "${local.rale_router_build_dir}"
      uv pip install --no-cache \
        --python-platform "${local.lambda_pip_platform}" --python-version 3.12 --only-binary :all: \
        -r "${local.rale_router_requirements}" --target "${local.rale_router_build_dir}"
      cp -R "${local.rale_router_source_dir}/." "${local.rale_router_build_dir}/"
    EOT
  }
}

data "archive_file" "raja_layer_zip" {
  type        = "zip"
  source_dir  = local.layer_build_dir
  output_path = "${local.build_dir}/raja_layer.zip"

  depends_on = [null_resource.build_raja_layer]
}

data "archive_file" "control_plane_zip" {
  type        = "zip"
  source_dir  = local.control_plane_build_dir
  output_path = "${local.build_dir}/control_plane.zip"

  depends_on = [null_resource.build_control_plane]
}

data "archive_file" "rale_authorizer_zip" {
  type        = "zip"
  source_dir  = local.rale_authorizer_build_dir
  output_path = "${local.build_dir}/rale_authorizer.zip"

  depends_on = [null_resource.build_rale_authorizer]
}

data "archive_file" "rale_router_zip" {
  type        = "zip"
  source_dir  = local.rale_router_build_dir
  output_path = "${local.build_dir}/rale_router.zip"

  depends_on = [null_resource.build_rale_router]
}

resource "aws_iam_role" "datazone_domain_execution" {
  name = local.datazone_domain_exec_role

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "datazone.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "datazone_domain_execution" {
  role       = aws_iam_role.datazone_domain_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonDataZoneDomainExecutionRolePolicy"
}

resource "aws_datazone_domain" "raja" {
  name                  = var.datazone_domain_name
  description           = "RAJA package authorization POC"
  domain_execution_role = aws_iam_role.datazone_domain_execution.arn
  skip_deletion_check   = true

  depends_on = [aws_iam_role_policy_attachment.datazone_domain_execution]
}

resource "aws_datazone_project" "owner" {
  domain_identifier   = aws_datazone_domain.raja.id
  name                = var.datazone_owner_project_name
  description         = "Owns RAJA-managed package listings"
  skip_deletion_check = true
}

resource "aws_datazone_asset_type" "quilt_package" {
  domain_identifier         = aws_datazone_domain.raja.id
  owning_project_identifier = aws_datazone_project.owner.id
  name                      = var.datazone_package_asset_type
  description               = "RAJA Quilt package access unit"
}

resource "aws_dynamodb_table" "policy_scope_mappings" {
  name         = "${var.stack_name}-policy-scope-mappings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "policy_id"

  attribute {
    name = "policy_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "principal_scopes" {
  name         = "${var.stack_name}-principal-scopes"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "principal"

  attribute {
    name = "principal"
    type = "S"
  }
}

resource "aws_dynamodb_table" "audit_log" {
  name         = "${var.stack_name}-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "event_id"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}


resource "aws_secretsmanager_secret" "jwt" {
  name                    = "${var.stack_name}-jwt-signing-key"
  description             = "JWT signing secret for RAJA token issuance"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "jwt_value" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = random_password.jwt_secret.result
}



resource "aws_iam_role" "control_plane_lambda" {
  name = "${var.stack_name}-control-plane-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "control_plane_basic_execution" {
  role       = aws_iam_role.control_plane_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "control_plane_permissions" {
  name = "${var.stack_name}-control-plane-policy"
  role = aws_iam_role.control_plane_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.policy_scope_mappings.arn,
          aws_dynamodb_table.principal_scopes.arn,
          aws_dynamodb_table.audit_log.arn,
          "${aws_dynamodb_table.audit_log.arn}/index/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.jwt.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionConfiguration",
          "lambda:GetFunctionConcurrency",
          "lambda:PutFunctionConcurrency",
          "lambda:DeleteFunctionConcurrency"
        ]
        Resource = [
          local.control_plane_lambda_arn,
          local.rale_authorizer_lambda_arn,
          local.rale_router_lambda_arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "datazone:AcceptSubscriptionRequest",
          "datazone:CreateProject",
          "datazone:CreateSubscriptionRequest",
          "datazone:ListProjects",
          "datazone:ListSubscriptionRequests",
          "datazone:SearchListings",
          "datazone:Search"
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_layer_version" "raja" {
  layer_name               = "${var.stack_name}-raja-layer"
  filename                 = data.archive_file.raja_layer_zip.output_path
  source_code_hash         = data.archive_file.raja_layer_zip.output_base64sha256
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = [var.lambda_architecture]
  description              = "Shared RAJA library for Lambda handlers"
}

resource "aws_lambda_function" "control_plane" {
  function_name = "${var.stack_name}-control-plane"
  role          = aws_iam_role.control_plane_lambda.arn
  runtime       = "python3.12"
  architectures = [var.lambda_architecture]
  handler       = "handler.handler"
  timeout       = 15
  memory_size   = 512

  filename         = data.archive_file.control_plane_zip.output_path
  source_code_hash = data.archive_file.control_plane_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.raja.arn]

  environment {
    variables = {
      DATAZONE_DOMAIN_ID                   = aws_datazone_domain.raja.id
      DATAZONE_OWNER_PROJECT_ID            = aws_datazone_project.owner.id
      DATAZONE_PACKAGE_ASSET_TYPE          = aws_datazone_asset_type.quilt_package.name
      DATAZONE_PACKAGE_ASSET_TYPE_REVISION = aws_datazone_asset_type.quilt_package.revision
      MAPPINGS_TABLE                       = aws_dynamodb_table.policy_scope_mappings.name
      PRINCIPAL_TABLE                      = aws_dynamodb_table.principal_scopes.name
      AUDIT_TABLE                          = aws_dynamodb_table.audit_log.name
      JWT_SECRET_ARN                       = aws_secretsmanager_secret.jwt.arn
      JWT_SECRET_VERSION                   = aws_secretsmanager_secret_version.jwt_value.version_id
      TOKEN_TTL                            = tostring(var.token_ttl)
      RAJA_ADMIN_KEY                       = var.raja_admin_key
      RALE_AUTHORIZER_FUNCTION_NAME        = local.rale_authorizer_lambda_name
      RALE_ROUTER_FUNCTION_NAME            = local.rale_router_lambda_name
      AWS_ACCOUNT_ID                       = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.control_plane_basic_execution,
    aws_iam_role_policy.control_plane_permissions,
    aws_secretsmanager_secret_version.jwt_value
  ]
}

resource "aws_iam_role" "rale_authorizer_lambda" {
  name = "${var.stack_name}-rale-authorizer-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rale_authorizer_basic_execution" {
  role       = aws_iam_role.rale_authorizer_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "rale_authorizer_permissions" {
  name = "${var.stack_name}-rale-authorizer-policy"
  role = aws_iam_role.rale_authorizer_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "datazone:ListSubscriptionRequests",
          "datazone:SearchListings"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem"
        ]
        Resource = [
          aws_dynamodb_table.principal_scopes.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.rajee_registry.arn,
          "${aws_s3_bucket.rajee_registry.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.jwt.arn
        ]
      }
    ]
  })
}

resource "aws_lambda_function" "rale_authorizer" {
  function_name = "${var.stack_name}-rale-authorizer"
  role          = aws_iam_role.rale_authorizer_lambda.arn
  runtime       = "python3.12"
  architectures = [var.lambda_architecture]
  handler       = "handler.handler"
  timeout       = 20
  memory_size   = 512

  filename         = data.archive_file.rale_authorizer_zip.output_path
  source_code_hash = data.archive_file.rale_authorizer_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.raja.arn]

  environment {
    variables = {
      DATAZONE_DOMAIN_ID                   = aws_datazone_domain.raja.id
      DATAZONE_PACKAGE_ASSET_TYPE          = aws_datazone_asset_type.quilt_package.name
      DATAZONE_PACKAGE_ASSET_TYPE_REVISION = aws_datazone_asset_type.quilt_package.revision
      PRINCIPAL_TABLE                      = aws_dynamodb_table.principal_scopes.name
      JWT_SECRET_ARN                       = aws_secretsmanager_secret.jwt.arn
      JWT_SECRET_VERSION                   = aws_secretsmanager_secret_version.jwt_value.version_id
      TOKEN_TTL                            = tostring(var.token_ttl)
      RALE_STORAGE                         = var.rale_storage
      RALE_ACTION                          = "quilt:ReadPackage"
      RALE_ISSUER                          = local.issuer
      RALE_AUDIENCE                        = "raja-rale"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.rale_authorizer_basic_execution,
    aws_iam_role_policy.rale_authorizer_permissions,
    aws_secretsmanager_secret_version.jwt_value
  ]
}

resource "aws_lambda_function_url" "rale_authorizer" {
  function_name      = aws_lambda_function.rale_authorizer.function_name
  authorization_type = "NONE"
}

resource "aws_iam_role" "rale_router_lambda" {
  name = "${var.stack_name}-rale-router-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rale_router_basic_execution" {
  role       = aws_iam_role.rale_router_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "rale_router_permissions" {
  name = "${var.stack_name}-rale-router-policy"
  role = aws_iam_role.rale_router_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.rajee_test.arn,
          "${aws_s3_bucket.rajee_test.arn}/*",
          aws_s3_bucket.rajee_registry.arn,
          "${aws_s3_bucket.rajee_registry.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.jwt.arn
        ]
      }
    ]
  })
}

resource "aws_lambda_function" "rale_router" {
  function_name = "${var.stack_name}-rale-router"
  role          = aws_iam_role.rale_router_lambda.arn
  runtime       = "python3.12"
  architectures = [var.lambda_architecture]
  handler       = "handler.handler"
  timeout       = 30
  memory_size   = 1024

  filename         = data.archive_file.rale_router_zip.output_path
  source_code_hash = data.archive_file.rale_router_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.raja.arn]

  environment {
    variables = {
      JWT_SECRET_ARN     = aws_secretsmanager_secret.jwt.arn
      JWT_SECRET_VERSION = aws_secretsmanager_secret_version.jwt_value.version_id
      RALE_STORAGE       = var.rale_storage
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.rale_router_basic_execution,
    aws_iam_role_policy.rale_router_permissions,
    aws_secretsmanager_secret_version.jwt_value
  ]
}

resource "aws_lambda_function_url" "rale_router" {
  function_name      = aws_lambda_function.rale_router.function_name
  authorization_type = "NONE"
}

resource "aws_api_gateway_rest_api" "raja" {
  name = "${var.stack_name}-api"
}

resource "aws_api_gateway_resource" "proxy" {
  rest_api_id = aws_api_gateway_rest_api.raja.id
  parent_id   = aws_api_gateway_rest_api.raja.root_resource_id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "root_any" {
  rest_api_id   = aws_api_gateway_rest_api.raja.id
  resource_id   = aws_api_gateway_rest_api.raja.root_resource_id
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "root_any" {
  rest_api_id             = aws_api_gateway_rest_api.raja.id
  resource_id             = aws_api_gateway_rest_api.raja.root_resource_id
  http_method             = aws_api_gateway_method.root_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.control_plane.invoke_arn
}

resource "aws_api_gateway_method" "proxy_any" {
  rest_api_id   = aws_api_gateway_rest_api.raja.id
  resource_id   = aws_api_gateway_resource.proxy.id
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "proxy_any" {
  rest_api_id             = aws_api_gateway_rest_api.raja.id
  resource_id             = aws_api_gateway_resource.proxy.id
  http_method             = aws_api_gateway_method.proxy_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.control_plane.invoke_arn
}

resource "aws_lambda_permission" "allow_apigw_invoke" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.control_plane.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.raja.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "raja" {
  rest_api_id = aws_api_gateway_rest_api.raja.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_integration.root_any.id,
      aws_api_gateway_integration.proxy_any.id,
      aws_lambda_function.control_plane.qualified_arn
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.root_any,
    aws_api_gateway_integration.proxy_any
  ]
}

resource "aws_api_gateway_stage" "prod" {
  rest_api_id   = aws_api_gateway_rest_api.raja.id
  deployment_id = aws_api_gateway_deployment.raja.id
  stage_name    = "prod"
}

resource "aws_vpc" "rajee" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.stack_name}-rajee-vpc"
  }
}

resource "aws_subnet" "rajee_public" {
  for_each = {
    for index, az in local.azs : index => az
  }

  vpc_id                  = aws_vpc.rajee.id
  availability_zone       = each.value
  cidr_block              = cidrsubnet(aws_vpc.rajee.cidr_block, 8, each.key)
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.stack_name}-rajee-public-${each.key}"
  }
}

resource "aws_subnet" "rajee_private" {
  for_each = {
    for index, az in local.azs : index => az
  }

  vpc_id            = aws_vpc.rajee.id
  availability_zone = each.value
  cidr_block        = cidrsubnet(aws_vpc.rajee.cidr_block, 8, each.key + 10)

  tags = {
    Name = "${var.stack_name}-rajee-private-${each.key}"
  }
}

resource "aws_internet_gateway" "rajee" {
  vpc_id = aws_vpc.rajee.id

  tags = {
    Name = "${var.stack_name}-rajee-igw"
  }
}

resource "aws_route_table" "rajee_public" {
  vpc_id = aws_vpc.rajee.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.rajee.id
  }

  tags = {
    Name = "${var.stack_name}-rajee-public-rt"
  }
}

resource "aws_route_table_association" "rajee_public" {
  for_each       = aws_subnet.rajee_public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.rajee_public.id
}

resource "aws_eip" "rajee_nat" {
  domain = "vpc"

  tags = {
    Name = "${var.stack_name}-rajee-nat-eip"
  }
}

resource "aws_nat_gateway" "rajee" {
  subnet_id     = aws_subnet.rajee_public[0].id
  allocation_id = aws_eip.rajee_nat.id

  tags = {
    Name = "${var.stack_name}-rajee-nat"
  }

  depends_on = [aws_internet_gateway.rajee]
}

resource "aws_route_table" "rajee_private" {
  vpc_id = aws_vpc.rajee.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.rajee.id
  }

  tags = {
    Name = "${var.stack_name}-rajee-private-rt"
  }
}

resource "aws_route_table_association" "rajee_private" {
  for_each       = aws_subnet.rajee_private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.rajee_private.id
}

resource "aws_ecs_cluster" "rajee" {
  name = "${var.stack_name}-rajee-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecr_repository" "envoy" {
  name                 = "raja/envoy"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "envoy" {
  repository = aws_ecr_repository.envoy.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "null_resource" "build_push_envoy_image" {
  triggers = {
    source_hash = local.envoy_source_hash
    repository  = aws_ecr_repository.envoy.repository_url
    image_tag   = local.envoy_image_tag_effective
    aws_region  = var.aws_region
    enabled     = tostring(var.build_envoy_image)
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail

      if [ "${var.build_envoy_image}" != "true" ]; then
        echo "Skipping Envoy image build/push (build_envoy_image=false)"
        exit 0
      fi

      REPO_URI="${aws_ecr_repository.envoy.repository_url}"
      TAG="${local.envoy_image_tag_effective}"

      if aws ecr describe-images --repository-name "${aws_ecr_repository.envoy.name}" --image-ids imageTag="$TAG" --region "${var.aws_region}" >/dev/null 2>&1; then
        echo "Envoy image $REPO_URI:$TAG already exists; skipping build/push."
        exit 0
      fi

      docker build -f "${local.envoy_source_dir}/Dockerfile" -t "raja-envoy:$TAG" "${local.repo_root}"
      docker tag "raja-envoy:$TAG" "$REPO_URI:$TAG"
      aws ecr get-login-password --region "${var.aws_region}" | docker login --username AWS --password-stdin "$REPO_URI"
      docker push "$REPO_URI:$TAG"
    EOT
  }

  depends_on = [aws_ecr_lifecycle_policy.envoy]
}

resource "aws_s3_bucket" "rajee_test" {
  bucket        = "raja-poc-test-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "rajee_test" {
  bucket = aws_s3_bucket.rajee_test.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "rajee_test" {
  bucket = aws_s3_bucket.rajee_test.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "rajee_test" {
  bucket = aws_s3_bucket.rajee_test.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "rajee_registry" {
  bucket        = "raja-poc-registry-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "rajee_registry" {
  bucket = aws_s3_bucket.rajee_registry.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "rajee_registry" {
  bucket = aws_s3_bucket.rajee_registry.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "rajee_registry" {
  bucket = aws_s3_bucket.rajee_registry.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "rajee_registry_accessor" {
  count  = length(var.registry_accessor_arns) > 0 ? 1 : 0
  bucket = aws_s3_bucket.rajee_registry.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RegistryAccessorReadWrite"
        Effect = "Allow"
        Principal = {
          AWS = var.registry_accessor_arns
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectVersion",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.rajee_registry.arn,
          "${aws_s3_bucket.rajee_registry.arn}/*"
        ]
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.rajee_registry]
}

resource "aws_iam_role" "rajee_task_execution" {
  name = "${var.stack_name}-rajee-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rajee_task_execution" {
  role       = aws_iam_role.rajee_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "rajee_task" {
  name = "${var.stack_name}-rajee-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "rajee_task_permissions" {
  name = "${var.stack_name}-rajee-task-policy"
  role = aws_iam_role.rajee_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.rajee_test.arn,
          "${aws_s3_bucket.rajee_test.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.rajee_test.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = ["*"]
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "RAJEE"
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "rajee_envoy" {
  name              = "/ecs/${var.stack_name}-envoy"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "rajee" {
  family                   = "${var.stack_name}-rajee-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.rajee_task_cpu)
  memory                   = tostring(var.rajee_task_memory)
  execution_role_arn       = aws_iam_role.rajee_task_execution.arn
  task_role_arn            = aws_iam_role.rajee_task.arn

  runtime_platform {
    cpu_architecture        = var.ecs_cpu_architecture
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "EnvoyProxy"
      image     = "${aws_ecr_repository.envoy.repository_url}:${local.envoy_image_tag_effective}"
      essential = true
      cpu       = 128
      memory    = 256
      portMappings = [
        {
          containerPort = 10000
          protocol      = "tcp"
        },
        {
          containerPort = 9901
          protocol      = "tcp"
        }
      ]
      environment = [
        {
          name  = "ENVOY_LOG_LEVEL"
          value = "info"
        },
        {
          name  = "AUTH_DISABLED"
          value = var.auth_disabled ? "true" : "false"
        },
        {
          name  = "JWKS_ENDPOINT"
          value = local.jwks_endpoint
        },
        {
          name  = "RAJA_ISSUER"
          value = local.issuer
        },
        {
          name  = "RAJEE_PUBLIC_PATH_PREFIXES"
          value = local.rajee_public_path_prefix
        },
        {
          name  = "RAJEE_PUBLIC_GRANTS"
          value = join(",", local.rajee_public_grants)
        },
        {
          name  = "RALE_AUTHORIZER_URL"
          value = aws_lambda_function_url.rale_authorizer.function_url
        },
        {
          name  = "RALE_ROUTER_URL"
          value = aws_lambda_function_url.rale_router.function_url
        }
      ]
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:9901/ready || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.rajee_envoy.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "envoy"
        }
      }
    }
  ])

  depends_on = [
    aws_iam_role_policy_attachment.rajee_task_execution,
    aws_iam_role_policy.rajee_task_permissions,
    null_resource.build_push_envoy_image,
  ]
}

resource "aws_security_group" "rajee_alb" {
  name        = "${var.stack_name}-rajee-alb-sg"
  description = "ALB security group for RAJEE"
  vpc_id      = aws_vpc.rajee.id

  ingress {
    from_port   = var.certificate_arn == "" ? 80 : 443
    to_port     = var.certificate_arn == "" ? 80 : 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = length(var.admin_allowed_cidrs) > 0 ? [1] : []
    content {
      from_port   = 9901
      to_port     = 9901
      protocol    = "tcp"
      cidr_blocks = var.admin_allowed_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rajee_service" {
  name        = "${var.stack_name}-rajee-service-sg"
  description = "ECS service security group for RAJEE"
  vpc_id      = aws_vpc.rajee.id

  ingress {
    from_port       = 10000
    to_port         = 10000
    protocol        = "tcp"
    security_groups = [aws_security_group.rajee_alb.id]
  }

  ingress {
    from_port       = 9901
    to_port         = 9901
    protocol        = "tcp"
    security_groups = [aws_security_group.rajee_alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "rajee" {
  name               = substr("${replace(var.stack_name, "_", "-")}-rajee-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.rajee_alb.id]
  subnets            = [for subnet in aws_subnet.rajee_public : subnet.id]
}

resource "aws_lb_target_group" "rajee" {
  name        = substr("${replace(var.stack_name, "_", "-")}-rajee-tg", 0, 32)
  port        = 10000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.rajee.id

  health_check {
    enabled             = true
    path                = "/ready"
    port                = "9901"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
    matcher             = "200-399"
  }
}

resource "aws_lb_listener" "rajee_http" {
  count = var.certificate_arn == "" ? 1 : 0

  load_balancer_arn = aws_lb.rajee.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rajee.arn
  }
}

resource "aws_lb_listener" "rajee_https" {
  count = var.certificate_arn == "" ? 0 : 1

  load_balancer_arn = aws_lb.rajee.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rajee.arn
  }
}

resource "aws_lb_target_group" "rajee_admin" {
  count       = length(var.admin_allowed_cidrs) > 0 ? 1 : 0
  name        = substr("${replace(var.stack_name, "_", "-")}-rajee-admin", 0, 32)
  port        = 9901
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.rajee.id

  health_check {
    path     = "/ready"
    port     = "9901"
    protocol = "HTTP"
  }
}

resource "aws_lb_listener" "rajee_admin" {
  count             = length(var.admin_allowed_cidrs) > 0 ? 1 : 0
  load_balancer_arn = aws_lb.rajee.arn
  port              = 9901
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rajee_admin[0].arn
  }
}

resource "aws_ecs_service" "rajee" {
  name                               = "${var.stack_name}-rajee-service"
  cluster                            = aws_ecs_cluster.rajee.id
  task_definition                    = aws_ecs_task_definition.rajee.arn
  desired_count                      = 2
  launch_type                        = "FARGATE"
  health_check_grace_period_seconds  = 30
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = [for subnet in aws_subnet.rajee_private : subnet.id]
    security_groups  = [aws_security_group.rajee_service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.rajee.arn
    container_name   = "EnvoyProxy"
    container_port   = 10000
  }

  dynamic "load_balancer" {
    for_each = length(var.admin_allowed_cidrs) > 0 ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.rajee_admin[0].arn
      container_name   = "EnvoyProxy"
      container_port   = 9901
    }
  }

  depends_on = [
    aws_lb_listener.rajee_http,
    aws_lb_listener.rajee_https,
    aws_lb_listener.rajee_admin,
  ]
}

resource "aws_appautoscaling_target" "rajee" {
  max_capacity       = 10
  min_capacity       = 2
  resource_id        = "service/${aws_ecs_cluster.rajee.name}/${aws_ecs_service.rajee.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "rajee_cpu" {
  name               = "${var.stack_name}-rajee-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.rajee.resource_id
  scalable_dimension = aws_appautoscaling_target.rajee.scalable_dimension
  service_namespace  = aws_appautoscaling_target.rajee.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 70

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_policy" "rajee_requests" {
  name               = "${var.stack_name}-rajee-requests"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.rajee.resource_id
  scalable_dimension = aws_appautoscaling_target.rajee.scalable_dimension
  service_namespace  = aws_appautoscaling_target.rajee.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 1000

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.rajee.arn_suffix}/${aws_lb_target_group.rajee.arn_suffix}"
    }
  }
}

resource "aws_cloudwatch_dashboard" "rajee" {
  dashboard_name = "RAJEE-Monitoring"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Authorization Decisions"
          view    = "timeSeries"
          region  = var.aws_region
          stacked = false
          metrics = [
            ["RAJEE", "AuthorizationAllow", { stat = "Sum" }],
            ["RAJEE", "AuthorizationDeny", { stat = "Sum" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Authorization Latency"
          view    = "timeSeries"
          region  = var.aws_region
          stacked = false
          metrics = [
            ["RAJEE", "AuthorizationLatency", { stat = "Average" }]
          ]
        }
      }
    ]
  })
}
