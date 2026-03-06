# RAJA MVP with AWS CDK and Verified Permissions
## Design Analysis and Proposal

## Context

This document analyzes how AWS CDK (Python) and Amazon Verified Permissions (AVP) can support the RAJA MVP implementation, and proposes an architecture that validates the Software-Defined Authorization hypothesis.

---

## Amazon Verified Permissions Overview

### What is AVP?

Amazon Verified Permissions is a **scalable, fine-grained authorization service** that:
- Externalizes authorization logic from application code
- Centralizes policy management using Cedar policy language
- Provides authorization APIs that return ALLOW/DENY decisions
- Supports integration with identity providers (Cognito, OpenID Connect)

### Key AVP Components

1. **Policy Store**: Container for Cedar policies
2. **Cedar Policies**: Authorization rules written in Cedar language
3. **Policy Templates**: Reusable policy patterns
4. **Identity Source**: Integration with identity providers (optional)
5. **Authorization APIs**: Runtime decision-making endpoints

### How AVP Works

```
Application → IsAuthorized API → AVP Policy Store → Cedar Evaluation → ALLOW/DENY
```

**Important**: AVP evaluates Cedar policies **at runtime** on each authorization request.

---

## AWS CDK Python Support for AVP

### Available Constructs

The `aws_cdk.aws_verifiedpermissions` module provides:

1. **CfnPolicyStore** - Creates policy stores
2. **CfnPolicy** - Manages Cedar policies
3. **CfnPolicyTemplate** - Defines policy templates
4. **CfnIdentitySource** - Configures identity provider integration

### Limitations

**Low-level CloudFormation constructs only:**
- No high-level (L2/L3) CDK abstractions
- Direct mapping to CloudFormation resources
- Requires working with raw CloudFormation properties
- Limited helper methods and validation

**However**: CDKLabs provides a higher-level construct library:
- [cdk-verified-permissions](https://github.com/cdklabs/cdk-verified-permissions)
- L2-style constructs with better developer experience

---

## Tension with RAJA/SDA Hypothesis

### The Fundamental Question

**Can we use AVP to validate the RAJA hypothesis that authorization should be compiled rather than interpreted?**

### Problem: AVP is Runtime Interpretation

AVP's architecture is:
```
Request → Cedar Runtime → Policy Evaluation → Decision
```

This is **exactly what SDA argues against**:
- Cedar policies evaluated at runtime
- Context-dependent decision making
- Interpretation, not compilation

### RAJA/SDA Approach

The blog post proposes:
```
Control Plane: Cedar → Compile → JWT with Scopes
Data Plane: JWT + Request → Subset Check → Decision
```

Key difference: **Cedar is absent from the data plane**.

---

## Two Possible Architectures

### Architecture A: Pure RAJA (No AVP Runtime)

**Use AVP only as a control plane tool:**

1. **Policy Management**: Store Cedar policies in AVP Policy Store
2. **Compilation Service**: Custom service that:
   - Reads Cedar policies from AVP
   - Compiles policies to JWT tokens with explicit scopes
   - Issues tokens to principals
3. **Data Plane**: Custom enforcement (pure subset checking)
   - No AVP IsAuthorized calls
   - Token validation + scope checking only

**CDK Usage:**
- Create Policy Store for policy management
- No runtime AVP API calls
- AVP as "source of truth" for policies, not enforcement

**Pros:**
- True to RAJA/SDA hypothesis
- Mechanical enforcement without interpretation
- Demonstrates compiled authorization

**Cons:**
- Not using AVP's core feature (runtime evaluation)
- Need custom token compilation logic
- AVP becomes expensive policy storage

---

### Architecture B: Hybrid Approach

**Use AVP to validate the hypothesis, then prove optimization:**

1. **Phase 1 - Baseline**: Use AVP runtime evaluation
   - Call IsAuthorized API for each request
   - Measure latency, cost, determinism

2. **Phase 2 - RAJA Optimization**: Add compilation layer
   - Compile frequently-used policies to JWT tokens
   - Cache authorization decisions as scopes
   - Fall back to AVP for complex cases

3. **Phase 3 - Comparison**: Demonstrate improvement
   - Show compiled approach is faster
   - Show compiled approach is deterministic
   - Show compiled approach is more transparent

**CDK Usage:**
- Full AVP infrastructure
- Both runtime and compiled paths
- A/B comparison framework

**Pros:**
- Shows "before and after" of RAJA approach
- Validates hypothesis with empirical data
- Uses AVP as intended initially

**Cons:**
- More complex implementation
- Still requires custom compilation logic
- Hybrid architecture is harder to reason about

---

## Recommended Architecture: Pure RAJA (Architecture A)

### Rationale

The MVP should **validate the core hypothesis cleanly** rather than demonstrate optimization of an existing system.

**Why Architecture A:**
1. **Clear hypothesis test**: Does compilation work better than interpretation?
2. **Simpler implementation**: One authorization path, not two
3. **True to SDA principles**: Cedar absent from data plane
4. **Educational value**: Shows the alternative architecture clearly

### How to Use CDK and AVP

**AVP Role: Control Plane Only**
- Policy Store as centralized policy repository
- Cedar policies managed via CDK/Console
- Schema definitions for resources and actions
- Policy validation at storage time

**Custom Components:**
- Policy compiler service (reads AVP, emits JWTs)
- Token issuance service
- Data plane enforcement (subset checking)

**CDK Role:**
- Provision Policy Store
- Deploy Cedar policies as code
- Deploy Lambda functions for compilation/enforcement
- Configure API Gateway for token/auth endpoints

---

## CDK Architecture Components

### Infrastructure to Provision

1. **AVP Policy Store**
   - Stores Cedar policies
   - Defines schema (resource types, actions)
   - Validation configuration

2. **Policy Compiler Lambda**
   - Triggered by policy changes
   - Reads policies from AVP
   - Generates scope mappings
   - Stores in DynamoDB

3. **Token Service Lambda**
   - Issues JWT tokens
   - Looks up principal permissions
   - Compiles scopes from policy mappings
   - Signs with secret key

4. **Enforcement Service Lambda**
   - Validates JWT tokens
   - Performs subset checking
   - Returns ALLOW/DENY
   - No Cedar evaluation

5. **DynamoDB Tables**
   - Policy-to-scope mappings
   - Principal-to-scopes cache
   - Token metadata (optional)

6. **API Gateway**
   - POST /token - Token issuance
   - POST /authorize - Authorization decision
   - GET /introspect - Token inspection

7. **Secrets Manager**
   - JWT signing keys
   - Token encryption keys

### Data Flow

```
1. Policy Management:
   Developer → CDK Deploy → AVP Policy Store → Policy Compiler Lambda → DynamoDB

2. Token Issuance:
   Client → API Gateway → Token Service → Query DynamoDB → Issue JWT

3. Authorization:
   Client → API Gateway → Enforcement Service → Validate JWT → Subset Check → Decision
```

---

## Success Metrics

The MVP demonstrates RAJA hypothesis if:

### Compilation Evidence
✅ Cedar policies stored in AVP
✅ Policies compiled to JWT scopes offline
✅ Scopes are explicit and canonical

### Enforcement Evidence
✅ No Cedar evaluation at authorization time
✅ Pure subset checking in enforcement
✅ Deterministic results (same input → same output)

### Performance Evidence
✅ Authorization latency < 10ms (no policy evaluation overhead)
✅ Enforcement service has no AVP SDK dependency
✅ Token introspection reveals exact authorities

---

## Implementation Phases

### Phase 1: AVP Policy Store Setup
- CDK creates Policy Store
- Define schema (Document, User, Actions)
- Write sample Cedar policies
- Validate policies in AVP

### Phase 2: Policy Compiler
- Lambda reads policies from AVP
- Parses Cedar permit statements
- Generates scope strings (ResourceType:ID:Action)
- Stores mappings in DynamoDB

### Phase 3: Token Service
- Lambda issues JWT tokens
- Looks up principal → scopes from DynamoDB
- Includes standard JWT claims (sub, iat, exp)
- Signs with HS256 or RS256

### Phase 4: Enforcement Service
- Lambda validates JWT signature
- Checks expiration
- Performs subset checking
- Returns Decision object

### Phase 5: Testing & Validation
- Test all 5 scenarios from MVP spec
- Measure latency and determinism
- Verify Cedar is absent from enforcement
- Demonstrate token transparency

---

## Key Design Decisions

### Decision 1: AVP as Control Plane Only
**Choice**: Use AVP for policy management, not runtime evaluation
**Rationale**: Validates RAJA hypothesis of compiled authorization

### Decision 2: JWT Token Format
**Choice**: Standard JWT with custom `scopes` claim
**Rationale**: Industry standard, tooling support, easy inspection

### Decision 3: Scope String Format
**Choice**: `ResourceType:ResourceId:Action`
**Rationale**: Explicit, parseable, no ambiguity

### Decision 4: No Token Revocation (MVP)
**Choice**: Rely on expiration only
**Rationale**: Aligns with SDA principle of expiration over revocation

### Decision 5: Single AWS Region Deployment
**Choice**: Deploy all components in one region
**Rationale**: MVP simplicity, avoid distributed systems complexity

---

## What This MVP Will NOT Do

❌ Use AVP IsAuthorized API at runtime
❌ Complex Cedar features (variables, templates, conditions)
❌ Token refresh mechanisms
❌ Multi-region deployment
❌ High-availability configuration
❌ Production-grade security (secrets rotation, etc.)
❌ Monitoring and observability (beyond basic CloudWatch)
❌ Cost optimization
❌ Identity provider integration (Cognito/OIDC)

---

## Conclusion

**Yes, we can use Python CDK to build the RAJA MVP.**

**Yes, AVP can support this - but in an unconventional way:**
- AVP serves as the control plane (policy management)
- AVP is NOT used for runtime authorization
- Custom services handle compilation and enforcement

This architecture validates the RAJA/SDA hypothesis by demonstrating that authorization can be treated as compilation rather than interpretation, while using AWS managed services (AVP) for policy management.

The CDK provides infrastructure-as-code for:
- AVP Policy Store and policies
- Lambda functions for compilation and enforcement
- DynamoDB for scope mappings
- API Gateway for HTTP endpoints
- All supporting infrastructure

Next step: Detailed implementation plan with file structure and CDK stack design.
