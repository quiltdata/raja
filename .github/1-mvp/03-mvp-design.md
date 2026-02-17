# RAJA MVP Implementation Design
## Service Architecture and Repository Layout

## Purpose

This document provides the detailed design and repository structure for implementing the RAJA MVP. It serves as a guide for developers who will write the code.

---

## Design Principles

### 1. Separation of Concerns
- **Public Library** (`raja`): Core authorization logic, framework-agnostic
- **Testing**: Comprehensive test suite for hypothesis validation
- **Infrastructure** (`raja-poc`): AWS CDK stack for deployment

### 2. Clean Boundaries
- Library has NO AWS dependencies (pure Python)
- CDK stack has NO business logic
- Tests validate both library and deployed services

### 3. Type Safety
- All modules use Python type hints
- Pydantic models for data validation
- py.typed marker for type checking support

---

## Repository Layout

```
raja/
├── README.md                           # Project overview and quickstart
├── pyproject.toml                      # UV project configuration
├── .python-version                     # Python 3.12
│
├── specs/                              # Design documentation
│   └── 1-mvp/
│       ├── 01-mvp-spec.md             # MVP specification
│       ├── 02-mvp-cdk.md              # CDK analysis
│       └── 03-mvp-design.md           # This document
│
├── src/                                # Source code
│   └── raja/                           # Public library (installable package)
│       ├── __init__.py                # Public API exports
│       ├── py.typed                   # Type hint marker
│       │
│       ├── models.py                  # Core data models
│       ├── scope.py                   # Scope representation and parsing
│       ├── token.py                   # JWT token operations
│       ├── compiler.py                # Cedar → Scopes compiler
│       ├── enforcer.py                # Authorization enforcement
│       │
│       └── cedar/                     # Cedar policy handling
│           ├── __init__.py
│           ├── parser.py              # Cedar policy parser
│           └── schema.py              # Cedar schema definitions
│
├── tests/                              # Test suite
│   ├── __init__.py
│   │
│   ├── unit/                          # Unit tests (no external dependencies)
│   │   ├── test_models.py
│   │   ├── test_scope.py
│   │   ├── test_token.py
│   │   ├── test_compiler.py
│   │   ├── test_enforcer.py
│   │   └── test_cedar_parser.py
│   │
│   ├── integration/                   # Integration tests (AWS services)
│   │   ├── test_policy_store.py
│   │   ├── test_token_service.py
│   │   ├── test_enforcement_service.py
│   │   └── test_end_to_end.py
│   │
│   └── hypothesis/                    # RAJA hypothesis validation tests
│       ├── test_determinism.py        # Same input → same output
│       ├── test_compilation.py        # Cedar → JWT correctness
│       ├── test_fail_closed.py        # Unknown requests → DENY
│       └── test_transparency.py       # Token inspection reveals authorities
│
├── infra/                              # Infrastructure as code
│   └── raja_poc/                      # CDK application
│       ├── __init__.py
│       ├── app.py                     # CDK app entry point
│       │
│       ├── stacks/                    # CDK stacks
│       │   ├── __init__.py
│       │   ├── avp_stack.py           # AVP Policy Store
│       │   ├── services_stack.py      # Lambda functions, DynamoDB, API Gateway
│       │   └── networking_stack.py    # VPC, security groups (if needed)
│       │
│       └── constructs/                # Reusable CDK constructs
│           ├── __init__.py
│           ├── policy_store.py        # AVP Policy Store construct
│           ├── compiler_lambda.py     # Policy compiler Lambda
│           ├── token_service.py       # Token issuance Lambda
│           └── enforcer_lambda.py     # Enforcement Lambda
│
├── lambda_handlers/                    # AWS Lambda function code
│   ├── compiler/                      # Policy compiler Lambda
│   │   ├── handler.py                 # Lambda entry point
│   │   └── requirements.txt           # Lambda dependencies
│   │
│   ├── token_service/                 # Token issuance Lambda
│   │   ├── handler.py
│   │   └── requirements.txt
│   │
│   └── enforcer/                      # Enforcement Lambda
│       ├── handler.py
│       └── requirements.txt
│
├── policies/                           # Sample Cedar policies
│   ├── schema.cedar                   # Cedar schema definition
│   └── policies/
│       ├── document_read.cedar
│       ├── document_write.cedar
│       └── admin_full.cedar
│
└── scripts/                            # Utility scripts
    ├── deploy.sh                      # Deployment script
    ├── test_local.py                  # Local testing without AWS
    └── load_policies.py               # Load Cedar policies to AVP
```

---

## Part A: RAJA Service (Public Module)

### Overview

The `raja` package is a **pure Python library** with NO AWS dependencies. It can be:
- Installed via `pip install raja`
- Used in any Python application
- Tested without AWS credentials
- Deployed to any environment

### Key Modules

#### 1. `models.py` - Core Data Models

**Purpose**: Define all data structures using Pydantic

**Models to Define**:

**Scope**
- Represents a single permission: `ResourceType:ResourceId:Action`
- Parse from string
- Serialize to string
- Validation logic

**AuthRequest**
- Represents an authorization request
- Fields: `resource_type`, `resource_id`, `action`
- Optional context data

**Decision**
- Authorization decision result
- Fields: `allowed` (bool), `reason` (str), `matched_scope` (optional)

**Token**
- JWT token representation
- Fields: `subject`, `scopes`, `issued_at`, `expires_at`
- Not the JWT string itself, but the decoded claims

**CedarPolicy**
- Represents a parsed Cedar policy statement
- Fields: `effect` (permit/forbid), `principal`, `action`, `resource`, `conditions`

---

#### 2. `scope.py` - Scope Operations

**Purpose**: Scope string format and operations

**Responsibilities**:
- Parse scope strings: `"Document:doc123:read"` → components
- Format scopes: components → `"Document:doc123:read"`
- Validate scope format (regex or parsing)
- Scope comparison and subset checking

**Key Functions**:
- `parse_scope(scope_str: str) -> Scope`
- `format_scope(resource_type, resource_id, action) -> str`
- `is_subset(requested: Scope, granted: list[Scope]) -> bool`

---

#### 3. `token.py` - JWT Token Operations

**Purpose**: JWT creation, signing, validation

**Responsibilities**:
- Create JWT tokens with scopes claim
- Sign tokens (HS256 or RS256)
- Validate token signature
- Check token expiration
- Decode token to Token model

**Key Functions**:
- `create_token(subject: str, scopes: list[str], ttl: int, secret: str) -> str`
- `validate_token(token_str: str, secret: str) -> Token`
- `decode_token(token_str: str) -> dict` (without validation, for inspection)
- `is_expired(token: Token) -> bool`

**Dependencies**: PyJWT library

---

#### 4. `compiler.py` - Cedar to Scopes Compiler

**Purpose**: Compile Cedar policies to scope strings

**Responsibilities**:
- Parse Cedar policy statements
- Extract (principal, resource, action) triples
- Generate scope strings from policies
- Handle simple permit statements (no conditions initially)

**Key Functions**:
- `compile_policy(cedar_policy: str) -> dict[str, list[str]]`
  - Returns: `{"alice": ["Document:doc123:read"], ...}`
- `compile_policies(policies: list[str]) -> dict[str, list[str]]`
  - Aggregates multiple policies

**Simplifications for MVP**:
- Only handle `permit` statements (ignore `forbid`)
- Only handle exact principal/resource matches (no wildcards)
- No conditions or context evaluation
- Static policy analysis only

---

#### 5. `enforcer.py` - Authorization Enforcement

**Purpose**: Make authorization decisions

**Responsibilities**:
- Accept token + request
- Validate token (signature, expiration)
- Perform subset checking
- Return Decision with reason

**Key Functions**:
- `enforce(token_str: str, request: AuthRequest, secret: str) -> Decision`
- `check_scopes(request: AuthRequest, granted_scopes: list[str]) -> bool`

**Enforcement Logic**:
```
1. Validate token signature → if invalid, DENY
2. Check token expiration → if expired, DENY
3. Parse request into scope string
4. Check if requested scope in token.scopes
5. Return Decision(allowed=True/False, reason="...", matched_scope="...")
```

**Fail-Closed Behavior**:
- Invalid token → DENY
- Expired token → DENY
- Malformed request → DENY
- Scope not found → DENY
- Any error → DENY (never default to ALLOW)

---

#### 6. `cedar/` Package - Cedar Policy Handling

**Purpose**: Parse and understand Cedar policies

**`cedar/parser.py`**:
- Parse Cedar policy syntax (simplified)
- Extract permit/forbid statements
- Extract principal, action, resource
- Return structured CedarPolicy objects

**`cedar/schema.py`**:
- Define Cedar schema (resource types, actions)
- Validate policies against schema
- Generate scope format from schema

**Simplifications**:
- No full Cedar parser (use regex or simple parsing)
- Only handle MVP policy patterns
- No validation beyond syntax

---

### Public API (`__init__.py`)

**Export clean public interface**:
```python
# Models
from .models import Scope, AuthRequest, Decision, Token

# Core functions
from .compiler import compile_policy, compile_policies
from .enforcer import enforce
from .token import create_token, validate_token, decode_token

# Version
__version__ = "0.1.0"
```

---

## Part B: Testing

### Test Strategy

**Three test levels**:
1. **Unit tests**: Pure Python, no AWS, fast
2. **Integration tests**: Against deployed AWS services
3. **Hypothesis tests**: Validate RAJA/SDA claims

---

### Unit Tests (`tests/unit/`)

**Characteristics**:
- No network calls
- No AWS credentials needed
- Run in milliseconds
- 100% code coverage goal

**Test Files**:

**`test_models.py`**
- Pydantic model validation
- Serialization/deserialization
- Edge cases (empty strings, special characters)

**`test_scope.py`**
- Scope parsing: valid and invalid formats
- Scope formatting
- Subset checking logic
- Edge cases (case sensitivity, special characters)

**`test_token.py`**
- JWT creation and signing
- Token validation (signature, expiration)
- Token decoding
- Invalid token handling
- Expiration edge cases

**`test_compiler.py`**
- Cedar policy parsing
- Scope generation from policies
- Multiple policies aggregation
- Invalid policy handling
- Edge cases (empty policies, malformed syntax)

**`test_enforcer.py`**
- Enforcement logic with valid tokens
- Enforcement with expired tokens
- Enforcement with invalid tokens
- Subset checking scenarios
- Fail-closed behavior

**`test_cedar_parser.py`**
- Cedar syntax parsing
- Principal/action/resource extraction
- Schema validation
- Error handling

---

### Integration Tests (`tests/integration/`)

**Characteristics**:
- Require deployed AWS infrastructure
- Test actual Lambda functions and API Gateway
- Validate AWS service integration
- Slower (seconds to run)

**Test Files**:

**`test_policy_store.py`**
- AVP Policy Store access
- Reading Cedar policies from AVP
- Policy validation via AVP

**`test_token_service.py`**
- Call token service API endpoint
- Verify JWT tokens issued
- Verify scopes in tokens match policies
- Test error cases (unknown principal)

**`test_enforcement_service.py`**
- Call enforcement API endpoint
- Verify ALLOW/DENY decisions
- Test with valid/invalid tokens
- Test expiration behavior

**`test_end_to_end.py`**
- Full flow: policy → token → enforcement
- Multiple principals and resources
- Realistic scenarios

---

### Hypothesis Tests (`tests/hypothesis/`)

**Purpose**: Validate the core RAJA/SDA claims

**Test Files**:

**`test_determinism.py`**
- **Claim**: Same token + same request = same decision always
- Test: Make 1000 identical requests, verify identical responses
- Test: No timing dependencies
- Test: No random behavior

**`test_compilation.py`**
- **Claim**: Cedar policies compile to explicit scopes
- Test: Policy → token → scope inspection reveals exact authorities
- Test: No hidden permissions
- Test: No implicit grants

**`test_fail_closed.py`**
- **Claim**: Unknown requests are explicitly denied
- Test: Request unknown resource → DENY
- Test: Request unknown action → DENY
- Test: Malformed request → DENY
- Test: No default-allow behavior

**`test_transparency.py`**
- **Claim**: Token inspection reveals authorities
- Test: Decode token without validation
- Test: List all scopes granted
- Test: Verify scopes match policies
- Test: No opaque authorization state

**Success Criteria**: All hypothesis tests pass = RAJA hypothesis validated

---

## Part C: raja-poc CDK Stack

### Overview

The CDK stack provisions all AWS infrastructure needed for the MVP deployment.

**Stack Name**: `RajaPocStack`

**Region**: Single region (us-east-1 or us-west-2)

**Components**: AVP, Lambda, DynamoDB, API Gateway, Secrets Manager

---

### CDK Application Structure

#### `app.py` - CDK Entry Point

**Purpose**: Define CDK app and instantiate stacks

**Responsibilities**:
- Create CDK App
- Instantiate AVPStack
- Instantiate ServicesStack
- Pass dependencies between stacks
- Synthesize CloudFormation

**Stack Order**:
1. AVPStack (Policy Store)
2. ServicesStack (depends on AVPStack)

---

### Stack 1: AVPStack (`stacks/avp_stack.py`)

**Purpose**: Provision Amazon Verified Permissions resources

**Resources to Create**:

**1. Policy Store**
- CfnPolicyStore construct
- Validation mode: STRICT
- Schema definition (inline or from file)

**2. Cedar Schema**
- Define resource types: Document, User
- Define actions: read, write, delete
- Define entity relationships

**3. Sample Policies**
- CfnPolicy constructs for each policy
- Load from `policies/` directory
- Policies for testing (alice, bob, admin)

**Outputs**:
- Policy Store ID (for Lambda functions to read)
- Policy Store ARN

---

### Stack 2: ServicesStack (`stacks/services_stack.py`)

**Purpose**: Provision application services

**Dependencies**:
- AVP Policy Store ID from AVPStack

**Resources to Create**:

**1. DynamoDB Table: PolicyScopeMappings**
- Partition key: `policy_id` (string)
- Attributes: `scopes` (list), `updated_at` (timestamp)
- Purpose: Cache compiled policy → scope mappings

**2. DynamoDB Table: PrincipalScopes**
- Partition key: `principal` (string)
- Attributes: `scopes` (list), `updated_at` (timestamp)
- Purpose: Cache principal → scopes for token issuance

**3. Secrets Manager Secret: JwtSigningKey**
- Store JWT signing secret
- Auto-generate or use provided value
- Grant Lambda read access

**4. Lambda Function: PolicyCompilerFunction**
- Handler: `lambda_handlers/compiler/handler.lambda_handler`
- Environment variables:
  - `POLICY_STORE_ID`: from AVPStack
  - `MAPPINGS_TABLE`: PolicyScopeMappings table name
  - `PRINCIPAL_TABLE`: PrincipalScopes table name
- Permissions:
  - Read from AVP Policy Store
  - Write to DynamoDB tables
- Trigger: EventBridge rule (on policy changes) OR manual invoke

**5. Lambda Function: TokenServiceFunction**
- Handler: `lambda_handlers/token_service/handler.lambda_handler`
- Environment variables:
  - `PRINCIPAL_TABLE`: PrincipalScopes table name
  - `JWT_SECRET_ARN`: Secrets Manager ARN
  - `TOKEN_TTL`: 3600 (seconds)
- Permissions:
  - Read from DynamoDB PrincipalScopes
  - Read from Secrets Manager
- Integration: API Gateway POST /token

**6. Lambda Function: EnforcerFunction**
- Handler: `lambda_handlers/enforcer/handler.lambda_handler`
- Environment variables:
  - `JWT_SECRET_ARN`: Secrets Manager ARN
- Permissions:
  - Read from Secrets Manager
- Integration: API Gateway POST /authorize
- **Key**: NO DynamoDB access, NO AVP access

**7. API Gateway: RajaAPI**
- REST API (or HTTP API)
- Endpoints:
  - `POST /token` → TokenServiceFunction
    - Request: `{"principal": "alice"}`
    - Response: `{"token": "eyJ..."}`
  - `POST /authorize` → EnforcerFunction
    - Request: `{"token": "eyJ...", "request": {"resource_type": "Document", "resource_id": "doc123", "action": "read"}}`
    - Response: `{"allowed": true, "reason": "...", "matched_scope": "..."}`
  - `GET /health` → Simple Lambda health check
- CORS configuration for testing

**Outputs**:
- API Gateway URL
- Lambda function ARNs
- DynamoDB table names

---

### CDK Constructs (`constructs/`)

**Purpose**: Encapsulate reusable infrastructure patterns

**`policy_store.py`**
- High-level construct for AVP Policy Store
- Accepts schema and policies as constructor params
- Creates CfnPolicyStore and CfnPolicy resources
- Returns Policy Store ID

**`compiler_lambda.py`**
- Construct for Policy Compiler Lambda
- Bundles lambda_handlers/compiler code
- Configures IAM permissions
- Sets environment variables

**`token_service.py`**
- Construct for Token Service Lambda
- Similar pattern as compiler

**`enforcer_lambda.py`**
- Construct for Enforcer Lambda
- Minimal permissions (read secrets only)

---

### Lambda Handler Implementation (`lambda_handlers/`)

**Important**: Lambda handlers use the `raja` library

**Each handler directory**:
- `handler.py`: Lambda entry point
- `requirements.txt`: Include `raja` package + AWS SDK

**Handler Pattern**:
```python
from raja import compile_policy, create_token, enforce
import boto3
import os

def lambda_handler(event, context):
    # Use raja library functions
    # Access AWS services as needed
    # Return response
```

---

### Deployment Flow

**Step 1: Initialize**
```bash
uv sync                    # Install dependencies
uv pip install aws-cdk-lib # CDK library
```

**Step 2: Synthesize**
```bash
cd infra
cdk synth                  # Generate CloudFormation
```

**Step 3: Deploy**
```bash
cdk deploy --all           # Deploy all stacks
```

**Step 4: Load Policies**
```bash
python scripts/load_policies.py  # Upload Cedar policies to AVP
```

**Step 5: Compile Policies**
```bash
aws lambda invoke         # Invoke PolicyCompilerFunction
```

**Step 6: Test**
```bash
pytest tests/integration/ # Run integration tests
```

---

## Cedar Policies (`policies/`)

### Schema Definition (`schema.cedar`)

**Purpose**: Define Cedar schema for MVP

**Contents**:
- Entity types: `User`, `Document`
- Actions: `read`, `write`, `delete`
- No hierarchies or relationships for MVP

**Example Structure** (NOT CODE, just concept):
```
Entity User { }
Entity Document { }

Action "read" applies to [Document]
Action "write" applies to [Document]
Action "delete" applies to [Document]
```

### Sample Policies (`policies/*.cedar`)

**`document_read.cedar`**
- Grant alice read access to doc123

**`document_write.cedar`**
- Grant alice write access to doc123

**`admin_full.cedar`**
- Grant admin user full access to all documents

**Format**: Standard Cedar syntax
- Use explicit principal/resource IDs
- No conditions or context for MVP

---

## Implementation Order

### Phase 1: Core Library (src/raja/)
1. `models.py` - Define all data models
2. `scope.py` - Scope operations
3. `token.py` - JWT operations
4. `cedar/parser.py` - Basic Cedar parsing
5. `compiler.py` - Policy to scopes
6. `enforcer.py` - Enforcement logic
7. Unit tests for each module

### Phase 2: Local Testing (tests/)
1. Complete unit test suite
2. `scripts/test_local.py` - Local testing script
3. Validate library works without AWS

### Phase 3: Infrastructure (infra/)
1. `stacks/avp_stack.py` - AVP resources
2. `stacks/services_stack.py` - Services
3. `app.py` - CDK app
4. Synthesize and validate CloudFormation

### Phase 4: Lambda Handlers (lambda_handlers/)
1. `compiler/handler.py`
2. `token_service/handler.py`
3. `enforcer/handler.py`
4. Package and test locally

### Phase 5: Deployment
1. Deploy CDK stacks
2. Load Cedar policies
3. Invoke compiler Lambda
4. Run integration tests

### Phase 6: Hypothesis Validation (tests/hypothesis/)
1. Write hypothesis tests
2. Execute against deployed system
3. Validate RAJA claims
4. Document results

---

## Key Design Decisions

### Decision 1: Pure Python Library
**Why**: raja package can be used anywhere, not just AWS
**Impact**: Lambda handlers import raja library

### Decision 2: Separate Lambda Functions
**Why**: Clear separation of concerns, independent scaling
**Impact**: Three Lambda functions vs one monolith

### Decision 3: DynamoDB Caching
**Why**: Avoid repeated AVP policy reads
**Impact**: Eventual consistency acceptable for MVP

### Decision 4: JWT HS256 Signing
**Why**: Simpler than RS256, sufficient for MVP
**Impact**: Shared secret in Secrets Manager

### Decision 5: No Identity Provider Integration
**Why**: Out of scope for MVP
**Impact**: Manual principal specification in token requests

### Decision 6: Single Region
**Why**: MVP simplicity
**Impact**: No multi-region considerations

### Decision 7: REST API (not GraphQL)
**Why**: Simpler, standard HTTP
**Impact**: API Gateway REST API construct

---

## Success Metrics

### Code Quality
- [ ] Type hints on all functions
- [ ] Pydantic models for all data
- [ ] 90%+ test coverage
- [ ] All tests pass

### Deployment
- [ ] CDK synth succeeds
- [ ] CDK deploy succeeds
- [ ] All Lambda functions healthy
- [ ] API Gateway accessible

### Functionality
- [ ] Policies compile to scopes correctly
- [ ] Tokens issued with correct scopes
- [ ] Enforcement returns correct decisions
- [ ] All 5 test scenarios pass

### Hypothesis Validation
- [ ] Determinism test passes
- [ ] Compilation test passes
- [ ] Fail-closed test passes
- [ ] Transparency test passes

---

## Documentation Requirements

### Code Documentation
- Docstrings on all public functions
- Type hints required
- README in each major directory

### API Documentation
- API Gateway endpoint documentation
- Request/response examples
- Error codes and handling

### Deployment Documentation
- Step-by-step deployment guide
- Environment variables
- AWS permissions needed

### Testing Documentation
- How to run unit tests
- How to run integration tests
- How to interpret results

---

## Next Steps

1. **Review this design** - Ensure all stakeholders agree
2. **Set up development environment** - Install UV, Python 3.12, AWS CLI
3. **Create repository structure** - Directories and empty files
4. **Begin Phase 1** - Implement core library
5. **Iterate and validate** - Test as you build

This design provides the roadmap. The implementation is yours to execute.
