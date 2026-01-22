#!/bin/bash
# Build and push Envoy container image to ECR
# Usage: ./scripts/build-envoy-image.sh [--tag TAG] [--push] [--platform PLATFORM] [--print-tag]

set -euo pipefail

# Parse arguments
PUSH=false
IMAGE_TAG=""
PLATFORM=""
PRINT_TAG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --push)
            PUSH=true
            shift
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --print-tag)
            PRINT_TAG=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--tag TAG] [--push] [--platform PLATFORM] [--print-tag]"
            exit 1
            ;;
    esac
done

# Get repository root
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

compute_image_tag() {
    local -a files
    shopt -s nullglob
    files=(
        "infra/raja_poc/assets/envoy/Dockerfile"
        infra/raja_poc/assets/envoy/*.sh
        infra/raja_poc/assets/envoy/*.lua
        infra/raja_poc/assets/envoy/*.tmpl
    )
    local hash
    if [ ${#files[@]} -eq 0 ]; then
        echo "Error: no Envoy files found for hashing." >&2
        exit 1
    fi
    hash=$(cat "${files[@]}" | shasum -a 256 | cut -c1-8)
    shopt -u nullglob
    echo "$hash"
}

# Get content hash for tagging if not provided
if [ -z "$IMAGE_TAG" ]; then
    IMAGE_TAG=$(compute_image_tag)
    if [ "$PRINT_TAG" = false ]; then
        echo "No tag specified, using content hash: ${IMAGE_TAG}"
    fi
fi

if [ "$PRINT_TAG" = true ]; then
    echo "${IMAGE_TAG}"
    exit 0
fi

# Get ECR repository URI from CDK outputs
echo "Getting ECR repository URI from CloudFormation..."
REPO_URI=$(aws cloudformation describe-stacks \
    --stack-name RajeeEnvoyStack \
    --query 'Stacks[0].Outputs[?OutputKey==`EnvoyRepositoryUri`].OutputValue' \
    --output text 2>/dev/null)

if [ -z "$REPO_URI" ]; then
    echo "Error: Could not get ECR repository URI from CloudFormation."
    echo "Make sure RajeeEnvoyStack is deployed first with the ECR repository."
    echo ""
    echo "To deploy the stack:"
    echo "  cd infra && npx cdk deploy RajeeEnvoyStack"
    exit 1
fi

echo "ECR Repository: ${REPO_URI}"

# Extract AWS region from repository URI
AWS_REGION=$(echo "$REPO_URI" | cut -d'.' -f4)
echo "AWS Region: ${AWS_REGION}"

if [ "$PUSH" = true ]; then
    REPO_NAME="${REPO_URI#*/}"
    if aws ecr describe-images \
        --repository-name "${REPO_NAME}" \
        --image-ids imageTag="${IMAGE_TAG}" \
        --region "${AWS_REGION}" >/dev/null 2>&1; then
        echo ""
        echo "Image ${REPO_URI}:${IMAGE_TAG} already exists in ECR; skipping build and push."
        exit 0
    fi
fi

# Build image
echo ""
echo "Building image with tag: ${IMAGE_TAG}"
BUILD_CMD="docker build -f infra/raja_poc/assets/envoy/Dockerfile -t raja-envoy:${IMAGE_TAG} ."

if [ -n "$PLATFORM" ]; then
    BUILD_CMD="$BUILD_CMD --platform $PLATFORM"
    echo "Platform: ${PLATFORM}"
fi

eval $BUILD_CMD

# Tag for ECR
echo ""
echo "Tagging image for ECR..."
docker tag "raja-envoy:${IMAGE_TAG}" "${REPO_URI}:${IMAGE_TAG}"

# Push if requested
if [ "$PUSH" = true ]; then
    echo ""
    echo "Logging in to ECR..."
    aws ecr get-login-password --region "${AWS_REGION}" | \
        docker login --username AWS --password-stdin "${REPO_URI}"

    echo ""
    echo "Pushing image to ECR..."
    docker push "${REPO_URI}:${IMAGE_TAG}"

    echo ""
    echo "✓ Image pushed successfully!"
    echo ""
    echo "To deploy with this image, run:"
    echo "  export IMAGE_TAG=${IMAGE_TAG}"
    echo "  cd infra && npx cdk deploy RajeeEnvoyStack"
else
    echo ""
    echo "✓ Image built successfully!"
    echo ""
    echo "To push this image to ECR, run:"
    echo "  $0 --tag ${IMAGE_TAG} --push"
    echo ""
    echo "Or to build and push in one command:"
    echo "  $0 --push"
fi
