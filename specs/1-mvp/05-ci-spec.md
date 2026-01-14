# RAJA CI/CD Specification
## Continuous Integration and Deployment Pipeline

## Purpose

This document specifies the CI/CD pipeline for RAJA using GitHub Actions. The pipeline uses the standardized Poe tasks defined in [04-poe-tasks.md](04-poe-tasks.md) to ensure consistency between local development and CI environments.

---

## Design Principles

### 1. Use Poe Tasks
**All CI operations use `./poe` commands** - no duplication of logic

### 2. Fast Feedback
**Quick checks run first** - fail fast on lint/type errors before running slow tests

### 3. Test in Isolation
**Unit tests run without AWS** - integration tests only run when needed

### 4. Security First
**AWS credentials only when required** - minimal permission scopes

### 5. Branch Protection
**Main branch protected** - requires passing checks and review

---

## GitHub Actions Workflows

### Location
All workflows in `.github/workflows/`

### Workflows to Create
1. **`ci.yml`** - Main CI pipeline (lint, type check, test)
2. **`integration.yml`** - Integration tests against AWS
3. **`deploy.yml`** - Deployment to AWS environments
4. **`release.yml`** - Package release and publishing

---

## Workflow 1: Main CI Pipeline

### File: `.github/workflows/ci.yml`

### Trigger Events
- **Push** to any branch
- **Pull request** to `main` branch
- **Manual** workflow dispatch

### Jobs

#### Job 1: Code Quality (`quality`)

**Purpose**: Fast feedback on code formatting and linting

**Steps**:
1. Checkout code
2. Install UV
3. Setup Python 3.12
4. Install dependencies (`uv sync`)
5. Run `./poe format --check` (verify formatting)
6. Run `./poe lint` (check linting)
7. Run `./poe typecheck` (type checking)

**Runs on**: `ubuntu-latest`

**Fail fast**: Yes - stop pipeline if quality checks fail

**Cache**: UV cache, pip cache

---

#### Job 2: Unit Tests (`test-unit`)

**Purpose**: Run fast unit tests without AWS dependencies

**Depends on**: `quality` job passes

**Strategy**: Matrix testing
- Python versions: `[3.12, 3.13]`
- OS: `[ubuntu-latest, macos-latest]`

**Steps**:
1. Checkout code
2. Install UV
3. Setup Python (matrix version)
4. Install dependencies (`uv sync --extra dev`)
5. Run `./poe test-unit`
6. Upload test results
7. Upload coverage report

**Coverage reporting**:
- Use `pytest-cov` for coverage
- Upload to Codecov (optional)
- Comment coverage on PR

---

#### Job 3: Build Package (`build`)

**Purpose**: Verify package builds successfully

**Depends on**: `test-unit` job passes

**Steps**:
1. Checkout code
2. Install UV
3. Setup Python 3.12
4. Install dependencies
5. Run `./poe build`
6. Upload build artifacts (wheel, sdist)
7. Verify package metadata

**Artifacts**: Store for 30 days

---

### Complete CI Workflow Configuration

**Key Features**:
- Uses composite setup action for DRY
- Caches dependencies for speed
- Fails fast on quality issues
- Matrix tests across Python versions and OS
- Generates coverage reports
- Stores build artifacts

**Estimated Runtime**:
- Quality check: ~1 minute
- Unit tests: ~2-3 minutes per matrix combination
- Build: ~1 minute
- **Total**: ~5-8 minutes

---

## Workflow 2: Integration Tests

### File: `.github/workflows/integration.yml`

### Trigger Events
- **Push** to `main` branch (after merge)
- **Manual** workflow dispatch with environment selection
- **Scheduled**: Daily at 2 AM UTC

### Jobs

#### Job 1: Deploy Test Infrastructure (`deploy-test`)

**Purpose**: Deploy ephemeral test infrastructure to AWS

**Environment**: `test` (GitHub environment)

**AWS Credentials**:
- Use GitHub OIDC provider (no long-lived keys)
- Role: `arn:aws:iam::ACCOUNT:role/GitHubActionsRoleRajaTest`
- Permissions: CloudFormation, Lambda, DynamoDB, AVP, API Gateway

**Steps**:
1. Checkout code
2. Install UV and dependencies
3. Configure AWS credentials (OIDC)
4. Install AWS CDK CLI
5. Run `./poe cdk-deploy`
6. Save stack outputs (API Gateway URL, etc.)
7. Run `./poe load-policies`
8. Run `./poe compile-policies`

**Timeout**: 15 minutes

---

#### Job 2: Run Integration Tests (`test-integration`)

**Purpose**: Test against deployed AWS infrastructure

**Depends on**: `deploy-test` job succeeds

**Environment**: `test`

**Steps**:
1. Checkout code
2. Install UV and dependencies
3. Configure AWS credentials
4. Load stack outputs from previous job
5. Set environment variables (API_URL, etc.)
6. Run `./poe test-integration`
7. Upload test results

**Timeout**: 10 minutes

---

#### Job 3: Validate Hypothesis (`test-hypothesis`)

**Purpose**: Validate RAJA/SDA hypothesis claims

**Depends on**: `test-integration` job succeeds

**Steps**:
1. Load stack outputs
2. Run `./poe test-hypothesis`
3. Generate hypothesis validation report
4. Upload report as artifact

**Success Criteria**: All 4 hypothesis tests pass
- Determinism
- Compilation correctness
- Fail-closed behavior
- Transparency

---

#### Job 4: Cleanup Test Infrastructure (`cleanup-test`)

**Purpose**: Destroy ephemeral test infrastructure

**Depends on**: Always runs (even if tests fail)

**Condition**: `always()`

**Steps**:
1. Configure AWS credentials
2. Run `./poe cdk-destroy`
3. Verify resources deleted

**Timeout**: 10 minutes

---

### Integration Workflow Configuration

**Key Features**:
- Ephemeral infrastructure per run
- Uses GitHub OIDC for secure AWS access
- Always cleans up (even on failure)
- Validates RAJA hypothesis
- Saves test reports

**Estimated Runtime**:
- Deploy: ~5-8 minutes
- Integration tests: ~5 minutes
- Hypothesis tests: ~2-3 minutes
- Cleanup: ~3-5 minutes
- **Total**: ~15-20 minutes

---

## Workflow 3: Deployment

### File: `.github/workflows/deploy.yml`

### Trigger Events
- **Manual** workflow dispatch with environment input
- **Release** published (for production)

### Inputs
- `environment`: Choice of `dev`, `staging`, `prod`
- `confirm`: Boolean confirmation for prod deployments

### Jobs

#### Job 1: Validate Deployment (`validate`)

**Purpose**: Pre-deployment validation

**Steps**:
1. Checkout code
2. Run `./poe check` (quality checks)
3. Run `./poe test-unit`
4. Run `./poe build`
5. Run `./poe cdk-synth`
6. Review CloudFormation templates

---

#### Job 2: Deploy to Environment (`deploy`)

**Purpose**: Deploy to specified AWS environment

**Depends on**: `validate` job passes

**Environment**: `${{ inputs.environment }}` (uses GitHub environments)

**Steps**:
1. Checkout code
2. Configure AWS credentials (environment-specific role)
3. Install dependencies and CDK
4. Run `./poe cdk-diff` (show changes)
5. Wait for approval (if prod)
6. Run `./poe cdk-deploy`
7. Run `./poe load-policies`
8. Run `./poe compile-policies`
9. Smoke test deployment
10. Tag deployment in Git

**Production Safety**:
- Requires manual approval via GitHub environment protection
- Requires confirmation input
- Runs `cdk-diff` with approval wait time

---

#### Job 3: Verify Deployment (`verify`)

**Purpose**: Post-deployment verification

**Depends on**: `deploy` job succeeds

**Steps**:
1. Run health checks against deployed API
2. Run smoke tests
3. Monitor CloudWatch logs
4. Verify all Lambda functions healthy

**Rollback**: Manual process (documented separately)

---

### Deployment Workflow Configuration

**Key Features**:
- Environment-specific configurations
- Manual approval for production
- Shows infrastructure diff before deploying
- Post-deployment verification
- Git tags for tracking

**Estimated Runtime**:
- Validate: ~5 minutes
- Deploy: ~8-10 minutes
- Verify: ~2-3 minutes
- **Total**: ~15-18 minutes (plus approval wait time for prod)

---

## Workflow 4: Release

### File: `.github/workflows/release.yml`

### Trigger Events
- **Tag** push matching `v*` (e.g., `v0.1.0`)
- **Release** published on GitHub

### Jobs

#### Job 1: Build and Publish (`publish`)

**Purpose**: Build and publish raja package

**Steps**:
1. Checkout code with full history
2. Verify tag matches version in `pyproject.toml`
3. Run `./poe check`
4. Run `./poe test`
5. Run `./poe build`
6. Publish to PyPI (using trusted publishing)
7. Attach wheel and sdist to GitHub release
8. Update CHANGELOG

**PyPI Publishing**:
- Use GitHub OIDC trusted publishing (no API tokens)
- Configure in PyPI: raja project → trusted publisher → GitHub Actions

---

#### Job 2: Create Release Notes (`release-notes`)

**Purpose**: Generate release notes from changelog

**Steps**:
1. Extract release notes from CHANGELOG.md
2. Generate commit summary since last release
3. Update GitHub release description
4. Notify team (Slack, email, etc.)

---

### Release Workflow Configuration

**Key Features**:
- Automated PyPI publishing
- No API tokens needed (OIDC)
- Version validation
- Changelog automation
- GitHub release artifacts

---

## GitHub Environments

### Environment: `test`

**Purpose**: Ephemeral testing infrastructure

**Protection Rules**: None (auto-deploy)

**Secrets**:
- None (uses OIDC)

**Variables**:
- `AWS_REGION`: us-east-1
- `AWS_ACCOUNT_ID`: Test account ID

---

### Environment: `dev`

**Purpose**: Development environment

**Protection Rules**: None

**Secrets**: None (uses OIDC)

**Variables**:
- `AWS_REGION`: us-east-1
- `AWS_ACCOUNT_ID`: Dev account ID
- `STACK_NAME`: RajaPocStack-Dev

---

### Environment: `staging`

**Purpose**: Pre-production testing

**Protection Rules**:
- Required reviewers: 1

**Secrets**: None (uses OIDC)

**Variables**:
- `AWS_REGION`: us-east-1
- `AWS_ACCOUNT_ID`: Staging account ID
- `STACK_NAME`: RajaPocStack-Staging

---

### Environment: `prod`

**Purpose**: Production deployment

**Protection Rules**:
- Required reviewers: 2
- Wait timer: 5 minutes
- Deployment branches: `main` only

**Secrets**: None (uses OIDC)

**Variables**:
- `AWS_REGION`: us-east-1
- `AWS_ACCOUNT_ID`: Production account ID
- `STACK_NAME`: RajaPocStack-Prod

---

## AWS OIDC Configuration

### Setup Steps

**1. Create OIDC Provider in AWS IAM**
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

**2. Create IAM Role per Environment**

**Example Trust Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:quiltdata/raja:environment:test"
        }
      }
    }
  ]
}
```

**3. Attach Permissions Policy**
- CloudFormation full access
- Lambda full access
- DynamoDB full access
- Verified Permissions full access
- API Gateway full access
- Secrets Manager read access
- IAM limited (for Lambda role creation)
- CloudWatch Logs read access

**4. Configure in GitHub**
- Repository settings → Secrets and variables → Actions
- Add `AWS_ACCOUNT_ID` variable per environment
- GitHub Actions will automatically use OIDC

---

## Branch Protection Rules

### `main` Branch

**Required**:
- Pull request before merging
- At least 1 approval
- Status checks must pass:
  - `quality` job
  - `test-unit` job
  - `build` job
- Conversation resolution
- No force pushes
- No deletions

**Optional**:
- Require linear history
- Require signed commits

---

### Feature Branches

**Pattern**: `feature/*`, `fix/*`, `docs/*`

**Rules**:
- No protection (developer freedom)
- CI runs on push
- Encourage short-lived branches

---

## Composite Actions (DRY)

### Action: `setup-raja`

**File**: `.github/actions/setup-raja/action.yml`

**Purpose**: Reusable setup steps for all workflows

**Inputs**:
- `python-version`: Python version (default: 3.12)
- `install-extras`: Extra dependencies (default: dev)
- `cache`: Enable caching (default: true)

**Steps**:
1. Checkout code
2. Install UV
3. Setup Python with cache
4. Install dependencies (`uv sync --extra ${{ inputs.install-extras }}`)
5. Make `./poe` executable

**Usage in workflows**:
```yaml
- uses: ./.github/actions/setup-raja
  with:
    python-version: '3.12'
    install-extras: 'dev,aws'
```

---

## Secrets and Configuration

### Repository Secrets

**None required** - All use OIDC

### Repository Variables

- `AWS_ACCOUNT_ID_TEST`: Test account
- `AWS_ACCOUNT_ID_DEV`: Dev account
- `AWS_ACCOUNT_ID_STAGING`: Staging account
- `AWS_ACCOUNT_ID_PROD`: Production account
- `AWS_REGION`: Default region (us-east-1)

### PyPI Trusted Publishing

**Configure on PyPI**:
- Project: `raja`
- Publisher: GitHub Actions
- Repository: `quiltdata/raja`
- Workflow: `release.yml`
- Environment: Not specified (any)

---

## Monitoring and Notifications

### GitHub Checks

**All workflows create check runs** visible on PRs and commits

### Status Badges

**Add to README.md**:
```markdown
![CI](https://github.com/quiltdata/raja/workflows/CI/badge.svg)
![Integration Tests](https://github.com/quiltdata/raja/workflows/Integration/badge.svg)
![Coverage](https://codecov.io/gh/quiltdata/raja/branch/main/graph/badge.svg)
```

### Slack Notifications (Optional)

**On events**:
- Deployment success/failure
- Integration test failures
- Release published

**Implementation**: Use `slackapi/slack-github-action`

---

## Cost Optimization

### Strategy 1: Conditional Integration Tests

**Run full integration tests only on**:
- Push to `main`
- Manual trigger
- Daily schedule

**Skip on**:
- Feature branch pushes (unit tests only)
- Draft PRs

### Strategy 2: Ephemeral Infrastructure

**Create and destroy** test infrastructure per integration test run
- No idle resources
- No persistent costs

### Strategy 3: Parallel Matrix Testing

**Run unit tests in parallel** across Python versions and OS
- Faster feedback
- Efficient GitHub Actions minute usage

### Strategy 4: Cache Aggressively

**Cache**:
- UV dependencies
- Pip packages
- CDK synthesis results (where appropriate)

---

## Testing the CI Pipeline

### Local Simulation

**Use `act` to run GitHub Actions locally**:
```bash
# Install act
brew install act

# Run CI workflow locally
act push -W .github/workflows/ci.yml
```

### Gradual Rollout

**Phase 1**: CI workflow only (no AWS)
**Phase 2**: Add integration tests with manual trigger
**Phase 3**: Add automated deployment to dev
**Phase 4**: Add staging and production environments

---

## Troubleshooting Guide

### Common Issues

**1. OIDC Authentication Fails**
- Verify trust policy audience
- Check repository/environment name in condition
- Ensure OIDC provider exists in AWS account

**2. CDK Deploy Fails**
- Check IAM role permissions
- Verify AWS account ID
- Review CloudFormation stack events

**3. Integration Tests Timeout**
- Increase job timeout
- Check Lambda function logs
- Verify API Gateway endpoints reachable

**4. Cache Not Working**
- Verify cache key includes UV lock file hash
- Check cache restore step succeeds
- Ensure cache path is correct

---

## Success Metrics

### CI Pipeline Health

**Target SLAs**:
- CI pipeline: < 10 minutes (95th percentile)
- Integration tests: < 25 minutes
- Deployment: < 20 minutes
- Pipeline success rate: > 95%

**Monitoring**:
- GitHub Actions insights
- Track failure patterns
- Identify flaky tests

---

## Future Enhancements

### 1. Automated Rollback
**Trigger**: Deployment verification fails
**Action**: Automatically rollback to previous version

### 2. Progressive Deployment
**Strategy**: Canary deployments with traffic shifting
**Implementation**: AWS Lambda versions and aliases

### 3. Performance Testing
**Workflow**: Dedicated performance test suite
**Metrics**: Latency, throughput, error rate

### 4. Security Scanning
**Tools**: Snyk, Dependabot, CodeQL
**Frequency**: On every PR

### 5. Documentation Deployment
**Trigger**: Push to main
**Action**: Build and deploy Sphinx docs to GitHub Pages

---

## Summary

This CI/CD specification provides:

✅ **Four comprehensive workflows**: CI, Integration, Deploy, Release
✅ **Poe task integration**: Consistent commands across local and CI
✅ **Security**: OIDC authentication, no long-lived credentials
✅ **Fast feedback**: Fail fast on quality issues
✅ **Ephemeral testing**: No idle infrastructure costs
✅ **Environment protection**: Manual approvals for production
✅ **Automated releases**: Trusted publishing to PyPI

**Key Principle**: Everything uses `./poe` commands - no duplicate logic between local development and CI.

**Implementation Order**:
1. Create composite setup action
2. Implement CI workflow (quality + unit tests)
3. Add integration workflow (with manual trigger initially)
4. Setup AWS OIDC and environments
5. Add deployment workflow
6. Add release workflow
7. Configure branch protection

Next: Implementation of these workflows in `.github/workflows/` directory.
