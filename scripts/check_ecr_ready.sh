#!/bin/bash
# Check if ECR repository is ready for fast deployment
# Exit 0 if ready, exit 1 if needs bootstrap

# Check CDK outputs file first (fastest)
if [ -f "infra/cdk-outputs.json" ]; then
    REPO_URI=$(jq -r '.RajeeEnvoyStack.EnvoyRepositoryUri // empty' infra/cdk-outputs.json 2>/dev/null)
    if [ -n "$REPO_URI" ] && [ "$REPO_URI" != "None" ]; then
        exit 0
    fi
fi

# Fall back to CloudFormation API
REPO_URI=$(aws cloudformation describe-stacks \
    --stack-name RajeeEnvoyStack \
    --query 'Stacks[0].Outputs[?OutputKey==`EnvoyRepositoryUri`].OutputValue' \
    --output text 2>/dev/null)

if [ -n "$REPO_URI" ] && [ "$REPO_URI" != "None" ]; then
    exit 0
fi

# ECR not ready
exit 1
