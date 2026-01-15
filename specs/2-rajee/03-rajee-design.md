# RAJEE Testbed Design: Implementation Tasks

## Executive Summary

This document outlines the **key tasks** required to build RAJEE (RAJA Execution Environment) - a testbed demonstrating prefix-based authorization for S3 operations using Lambda REQUEST authorizer.

**Design Principles:**

- Authorization is pure prefix matching: `request.startswith(grant)`
- No wildcards, no patterns, no special characters
- Fail-closed by default
- Zero policy/database calls on the hot path

## Core Architecture

```
┌──────────────────┐
│  Control Plane   │  Mint JWT tokens with grants
│  (Already Exists)│  Format: "action/bucket/prefix"
└────────┬─────────┘
         │ Client gets JWT
         ▼
┌──────────────────┐
│  Data Plane      │  Lambda REQUEST Authorizer
│  (To Build)      │  → Prefix check: request.startswith(grant)
└────────┬─────────┘  → Return IAM policy
         │
         ▼
┌──────────────────┐
│  Protected S3    │  Only accessible via authorized API
└──────────────────┘
```

## Key Tasks

### Task 1: Update Token Structure

**What:** Change JWT claims from `scopes` to `grants` with prefix format

**Current format:**

```
scopes: ["S3:bucket:GetObject"]
```

**New format:**

```
grants: ["s3:GetObject/bucket/", "s3:PutObject/bucket/uploads/"]
```

**Files to modify:**

- Token issuance API
- JWT claim structure
- Token validation logic

### Task 2: Implement Prefix Authorization Logic

**What:** Create pure prefix matching function

**Logic:**

```
For each grant in token:
    If request.startswith(grant):
        return ALLOW
return DENY
```

**Requirements:**

- No wildcards
- No pattern matching
- Pure string prefix comparison
- Action must match exactly before path comparison

**Files to create/modify:**

- Core authorization function
- Unit tests for prefix matching
- Property-based tests (Hypothesis)

### Task 3: Create Lambda REQUEST Authorizer

**What:** Build Lambda function that validates JWT and performs prefix check

**Flow:**

1. Extract JWT from `Authorization: Bearer <token>` header
2. Verify JWT signature using shared secret
3. Extract grants from JWT claims
4. Construct request string from API Gateway event:
   - Method → Action (GET = GetObject, PUT = PutObject, etc.)
   - Path params → `action/bucket/key`
5. Check: `any(request.startswith(grant) for grant in grants)`
6. Return IAM policy document (Allow/Deny)

**Files to create:**

- Lambda handler function
- Request string builder
- IAM policy generator
- Unit tests for handler

### Task 4: Create Data Plane Lambda Handler

**What:** Build Lambda that performs actual S3 operations

**Flow:**

1. Receive authorized request from API Gateway
2. Extract bucket/key from path parameters
3. Perform S3 operation based on HTTP method:
   - GET → GetObject
   - PUT → PutObject
   - DELETE → DeleteObject
   - HEAD → HeadObject
4. Return result to client

**Files to create:**

- Lambda handler function
- S3 operation handlers
- Error handling
- Unit tests

### Task 5: Deploy Data Plane Infrastructure

**What:** Create CDK stacks for Lambda authorizer + API Gateway + S3

**Components:**

- Protected S3 bucket (block all public access)
- Lambda authorizer function
- Lambda data plane function
- API Gateway REST API with REQUEST authorizer
- API routes: `/s3/{bucket}/{key+}`

**Files to create:**

- Data plane CDK stack
- Data plane CDK construct
- Infrastructure wiring

### Task 6: Add JWKS Endpoint

**What:** Provide standard OIDC JWKS endpoint for JWT verification

**Endpoint:** `GET /.well-known/jwks.json`

**Returns:** Public key information for JWT signature verification

**Files to modify:**

- Control plane router
- Add JWKS endpoint

### Task 7: Create Integration Tests

**What:** Build end-to-end tests validating the complete flow

**Test scenarios:**

1. Mint token with read grant → GET object → Should succeed
2. Same token → PUT object → Should fail (403)
3. Mint token with write grant to prefix → PUT to prefix → Should succeed
4. Same token → PUT outside prefix → Should fail (403)
5. Mint token with multiple grants → Verify each works independently
6. Expired token → Should fail (403)
7. Invalid token → Should fail (403)
8. Missing token → Should fail (401)

**Test properties:**

- No AVP calls on hot path
- No DynamoDB calls on hot path
- Authorizer latency < 50ms
- Fail-closed behavior

**Files to create:**

- Integration test suite
- Test fixtures
- Helper functions

### Task 8: Update Documentation

**What:** Document the new prefix-based authorization system

**Content:**

- Grant format specification
- Request format specification
- Trailing slash convention
- Authorization algorithm
- Token issuance API
- Security considerations
- Usage examples

**Files to create/modify:**

- Design documentation
- API documentation
- User guide

## Success Criteria

### Functional Requirements

- [ ] Client can mint token with grants via control plane API
- [ ] Lambda authorizer validates JWT and performs prefix check
- [ ] Authorized S3 operations succeed
- [ ] Unauthorized operations fail with 403
- [ ] Out-of-scope requests denied solely by prefix logic
- [ ] JWKS endpoint available at `/.well-known/jwks.json`

### Non-Functional Requirements

- [ ] Zero AVP calls on hot path
- [ ] Zero DynamoDB calls on hot path
- [ ] Authorizer latency < 50ms p99
- [ ] Fail-closed: errors result in DENY
- [ ] 100% test coverage for authorization logic

### Security Requirements

- [ ] JWT signature verified on every request
- [ ] Token expiration enforced
- [ ] No token information leaked in error messages
- [ ] Protected bucket not directly accessible
- [ ] All S3 access goes through authorized API Gateway

## Implementation Order

### Phase 1: Core Logic (No AWS)

1. Update token structure (`grants` format)
2. Implement prefix authorization function
3. Write comprehensive unit tests
4. Add property-based tests

### Phase 2: Lambda Authorizer (Local Testing)

1. Create authorizer handler
2. Build request string constructor
3. Write unit tests with mock events
4. Test locally with sample tokens

### Phase 3: Data Plane Handler (Local Testing)

1. Create data plane handler
2. Implement S3 operation routing
3. Write unit tests with mock S3
4. Test error handling

### Phase 4: Infrastructure (AWS Deployment)

1. Create data plane CDK stack
2. Wire authorizer to API Gateway
3. Deploy to AWS
4. Verify deployment

### Phase 5: Integration (End-to-End)

1. Write integration tests
2. Run against deployed infrastructure
3. Validate success criteria
4. Performance testing

### Phase 6: Documentation

1. Update design docs
2. Write user guide
3. Add inline code comments
4. Create usage examples

## Testing Strategy

### Unit Tests

- Prefix matching logic
- Request string construction
- Token validation
- S3 operation handlers
- Error handling

### Integration Tests

- Full authorization flow
- Multiple grant scenarios
- Security tests (expired tokens, invalid signatures)
- Error conditions

### Property-Based Tests

- Prefix matching always works for valid prefixes
- Different actions never match
- No false positives
- No false negatives

### Performance Tests

- Authorizer latency measurement
- CloudWatch metrics validation
- Load testing (if needed)

## Security Considerations

### JWT Security

- Use strong signing secret (from Secrets Manager)
- Rotate secrets periodically
- Short token expiration (5 minutes)
- Verify signature on every request

### Prefix Security

- No special characters in grants
- Trailing slash convention prevents ambiguity
- Request strings constructed from API Gateway (not user input)
- Path traversal not applicable (URL structure enforced)

### Fail-Closed Design

- Unknown requests → DENY
- Parsing errors → DENY
- Validation errors → DENY
- Missing grants → DENY

## Grant Format Reference

### Format

```
{action}/{bucket}/{prefix}
```

### Examples

```
s3:GetObject/my-bucket/                    # Read entire bucket
s3:PutObject/my-bucket/uploads/            # Write to uploads/ prefix
s3:DeleteObject/my-bucket/uploads/user123/ # Delete from specific user path
s3:GetObject/my-bucket/doc.txt             # Read specific file
```

### Trailing Slash Rules

- Prefix grants: MUST end with `/`
- Exact file grants: MUST NOT end with `/`

### Action Names

- `s3:GetObject` - Read object
- `s3:PutObject` - Write object
- `s3:DeleteObject` - Delete object
- `s3:HeadObject` - Get object metadata

## Estimated Effort

| Phase | Effort | Risk |
|-------|--------|------|
| Core Logic | 4-6 hours | Low |
| Lambda Authorizer | 1 day | Medium |
| Data Plane Handler | 4-6 hours | Low |
| Infrastructure | 4-6 hours | Low |
| Integration Tests | 4-6 hours | Low |
| Documentation | 2-3 hours | Low |

**Total: 3-4 days**

## Files to Create

```
lambda_handlers/
├── authorizer/
│   └── handler.py           # Lambda REQUEST authorizer
└── data_plane/
    └── handler.py           # S3 operations handler

infra/raja_poc/
├── stacks/
│   └── data_plane_stack.py  # CDK stack for data plane
└── constructs/
    └── data_plane.py        # CDK construct for Lambda + API Gateway

tests/
├── unit/
│   ├── test_prefix_auth.py      # Prefix matching tests
│   ├── test_authorizer.py       # Authorizer handler tests
│   └── test_data_plane.py       # Data plane handler tests
└── integration/
    └── test_rajee_e2e.py         # End-to-end tests

specs/2-rajee/
└── 04-user-guide.md              # Usage documentation (future)
```

## Files to Modify

```
src/raja/
├── token.py                 # Update JWT claims structure
└── server/routers/
    ├── control_plane.py     # Add JWKS endpoint
    └── harness.py           # Update token minting API

pyproject.toml               # Update version (if needed)
```

## Next Steps

1. Review this design with team
2. Begin Phase 1 (Core Logic)
3. Iterate based on testing results
4. Deploy to AWS and validate
5. Document lessons learned

## References

- [01-rajee-gap.md](01-rajee-gap.md) - Gap analysis and current state
- [02-prefix-authorization-design.md](02-prefix-authorization-design.md) - Detailed prefix design
- GitHub Issue #5 - Original walking skeleton requirements
