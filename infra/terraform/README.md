# RAJA Single-Stack Terraform

This directory contains one Terraform root module that replaces all three CDK stacks:

- `RajaAvpStack`
- `RajaServicesStack`
- `RajeeEnvoyStack`

This is a full-stack deployment, not a control-plane-only module.

It provisions:

- Amazon Verified Permissions policy store, Cedar schema, and Cedar policies
- Control plane Lambda and shared RAJA Lambda layer
- API Gateway (`/` and `/{proxy+}` via Lambda proxy integration)
- DynamoDB tables (`policy_scope_mappings`, `principal_scopes`, `audit_log`)
- Secrets Manager secrets for JWT signing and harness signing
- RAJEE networking (VPC, public/private subnets, IGW, NAT)
- ECS/Fargate Envoy service with autoscaling and ALB
- ECR repository for Envoy images
- RAJEE S3 test bucket and CloudWatch dashboard

## Usage

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
terraform output -json legacy_cdk_outputs > ../cdk-outputs.json
```

Set `aws_region` in `terraform.tfvars` or via `TF_VAR_aws_region`.

## Envoy Image Workflow

By default, Terraform auto-builds and pushes Envoy during apply (`build_envoy_image=true`), using:

- `envoy_image_tag` if provided
- otherwise a content-hash tag derived from `infra/raja_poc/assets/envoy/*`

You can still build manually:

```bash
./poe build-envoy-push
export TF_VAR_envoy_image_tag=$(bash scripts/build-envoy-image.sh --print-tag)
export TF_VAR_build_envoy_image=false
./poe deploy
```

If you want HTTPS on RAJEE, set `certificate_arn` in `terraform.tfvars`.

## Notes

- Lambda artifacts are built locally during apply using `python3` (override with `python_bin`).
- `legacy_cdk_outputs` preserves compatibility with scripts/tests that read `infra/cdk-outputs.json`.
- The AWS Terraform provider does not currently expose policy store schema config, so apply runs a local post-create step that calls AVP APIs (`PutSchema`, `UpdatePolicyStore`) to set Cedar schema and enable `STRICT` validation before policy resources are created.
