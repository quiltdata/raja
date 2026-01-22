# Package Hardening and Optimization (Deferred)

This document contains deferred items from the initial gap analysis that focus on security hardening, performance optimization, and operational concerns. These should be addressed after the core functionality is complete and tested.

---

## Security Hardening

### QuiltUri Validation Edge Cases

**Tested Scenarios:**

- Basic parsing (test_quilt_uri.py:6-14)
- Path parameter (test_quilt_uri.py:17-21)
- Normalization (test_quilt_uri.py:24-28)
- Invalid URIs (test_quilt_uri.py:31-41) - only 3 cases

**UNTESTED Scenarios:**

- Very long URIs (>2048 chars)
- URIs with special characters in package name
- URIs with Unicode in registry or path
- URIs with multiple `@` symbols
- URIs with malformed hashes (invalid characters, wrong length)
- URIs with path traversal attempts (e.g., `path=../../etc/passwd`)
- URIs with injection attempts (`registry#package=x@h&path=y;malicious`)
- Case sensitivity edge cases
- Empty path parameter vs absent path parameter

**Impact:** Potential security vulnerabilities (path traversal, injection) or unexpected parsing failures

**Recommendation:** Add security-focused URI validation tests

### QuiltUri Injection Vulnerabilities

**Potential Attack Vectors:**

- Path traversal in `path` parameter: `quilt+s3://registry#package=x@h&path=../../sensitive`
- Command injection if URI components used in shell commands
- SQL/NoSQL injection if URI stored in database
- SSRF if registry parsed as URL and fetched

**Current Mitigation:**

- URI parsing uses `urlsplit` (quilt_uri.py:38)
- Path normalization replaces backslashes (quilt_uri.py:19)

**Gaps:**

- No validation of path traversal sequences (`..`, absolute paths)
- No validation of bucket/key format against S3 requirements
- No length limits on URI components

**Recommendation:** Add security tests for injection attempts, validate against S3 naming rules

### Token Claim Validation Completeness

**Current Validation:**

- quilt_uri: validates format and hash presence (token.py:144-151, 189-195)
- mode: validates against whitelist (token.py:154-155, 198-199)
- logical_bucket/logical_key: validates non-empty (token.py:216-218)

**Gaps:**

- No validation of quilt_uri length (JWT size limit)
- No validation of subject format
- logical_s3_path can override logical_bucket/logical_key (token.py:204-214) - conflict detection tested but not comprehensive
- No validation that quilt_uri registry is trusted
- No validation of timestamp claims (iat, exp, nbf) beyond JWT library defaults

**Recommendation:** Add stricter claim validation, consider whitelist of allowed registries

### Error Information Leakage

**Current Behavior:**

- Token validation errors return generic messages (token.py:131, 175, 255)
- Enforcement returns detailed reason strings

**Potential Issues:**

- Decision reason "object not in package" could leak package structure info
- Decision reason "logical key not mapped in package" reveals mapping gaps
- Token validation errors might reveal token structure expectations

**Fail-Closed Check:** ✅ All errors result in DENY (good)

**Recommendation:** Review error messages to ensure they don't leak sensitive info. Consider different verbosity levels for internal vs external errors.

### Package Integrity

**Specification Says:** (01-package-grant.md, lines 673-683)

- Packages stored in trusted, immutable storage
- quilt3 validates package signatures/hashes
- RAJEE only trusts packages from authorized registries

**Implementation Reality:**

- No implementation yet (manifest resolution not built)
- Dependency on quilt3 for integrity checks

**Recommendation:** Ensure quilt3 integration validates package integrity when implemented

---

## Performance & Scalability

### Large Package Handling

**Specification Says:** (01-package-grant.md, lines 823-827)

- Support packages with 10,000+ files
- Use caching to avoid repeated resolution

**Implementation Reality:**

- Enforcement logic does linear membership check (implied by callback design)
- No caching layer implemented
- No batching or optimization for large file lists
- No tests with large packages

**Potential Issues:**

- O(n) membership check for n files in package
- Repeated manifest resolution without cache
- Memory consumption for large file lists in PackageMap

**Recommendation:** Implement caching before production use with large packages

### Manifest Resolution Caching Strategy

**Specification Says:** (01-package-grant.md, lines 539-566)

- Cache resolved packages with infinite TTL (immutable)
- Cache key: hash(quilt_uri)
- Options: in-memory, Redis, DynamoDB

**Implementation Reality:**

- No caching implementation
- Callbacks are stateless (no cache injection mechanism)
- Every enforcement call would re-resolve manifest

**Recommendation:** Design caching layer when implementing manifest resolution

**Caching Design Considerations:**

1. **Cache Key:** `SHA256(quilt_uri)` - immutable, safe to cache forever
2. **Cache Value:** Serialized list of `(bucket, key)` tuples or PackageMap
3. **Cache Invalidation:** Never needed (packages are immutable)
4. **Implementation Options:**
   - **In-Memory (Lambda):** Fast but cold-start penalty, limited size
   - **Redis/ElastiCache:** Fast, shared across instances, requires network
   - **DynamoDB:** Durable, scalable, slightly higher latency
5. **Hybrid Approach:** In-memory L1 cache + DynamoDB L2 cache

**Performance Targets:**

- Package resolution: < 100ms p99 (cold), < 10ms p99 (warm with cache)
- Authorization decision: < 50ms p99 total

### PackageMap Translation Edge Cases

**Tested Scenarios:**

- Basic translation (test_package_map.py:5-14)
- Unknown key returns empty (test_package_map.py:17-22)

**UNTESTED Scenarios:**

- Empty logical key (should raise ValueError per line 17-18, but not tested)
- Whitespace-only logical key
- Logical key with special characters
- Logical key with path traversal attempts
- Case sensitivity in logical keys
- Very long logical keys
- Translation to multiple physical targets (spec mentions this)
- Physical targets in different buckets (cross-bucket packages)
- Circular translations (if that's possible)

**Impact:** Undefined behavior for edge cases, potential security issues

**Recommendation:** Add edge case tests

### Translation Grant Multi-Target Performance

**Specification Says:** (02-package-map.md, line 22)

- External authorizer returns "mapped physical target (bucket, key) or a small set of targets"

**Implementation Reality:**

- Decision model supports `translated_targets: list[S3Location]` (models.py:100)
- No guidance on "small set" limit
- No tests with multiple targets
- No optimization for single vs multiple targets

**Recommendation:** Define limits and add tests when needed

**Multi-Target Considerations:**

- What is the maximum number of targets? (suggest 10)
- How does RAJEE handle multiple targets? (execute first? all? return list to client?)
- Performance impact of multiple S3 requests

### Performance and Scale Testing

**Specification Says:** (01-package-grant.md, lines 808-827)

- Support packages with 10,000+ files
- Package resolution < 100ms p99 (cold), < 10ms p99 (warm)
- Authorization decision < 50ms p99 total

**Implementation Reality:**

- No performance tests for package enforcement
- No tests with large manifests
- No caching implementation for manifest resolution
- Existing performance test only for scope checking with 2000 scopes (test_enforcer.py:355-362)

**Impact:** Unknown performance characteristics. May not scale to large packages.

**Recommendation:** Add performance tests when manifest resolution is implemented

**Performance Test Scenarios:**

1. **Small Package** (10 files)
   - Cold start latency
   - Warm (cached) latency
   - Throughput (requests/sec)

2. **Medium Package** (1,000 files)
   - Cold start latency
   - Warm (cached) latency
   - Memory consumption

3. **Large Package** (10,000 files)
   - Cold start latency
   - Warm (cached) latency
   - Memory consumption
   - Cache serialization time

4. **Translation Grant** (PackageMap)
   - Translation latency for single target
   - Translation latency for multiple targets (2, 5, 10)
   - Manifest parsing time

---

## Operational Concerns

### Monitoring and Observability

**What's Needed:**

- Metrics for package resolution latency
- Cache hit/miss rates for manifests
- Package grant authorization outcomes (allow/deny counts)
- Error rates for malformed URIs or missing manifests

**Current State:**

- Structured logging exists (structlog) (enforcer.py:75)
- Log statements for package authorization (enforcer.py:223-242, 294-302)
- No metrics emission
- No distributed tracing integration

**Recommendation:** Add metrics and tracing when implementing manifest resolution

**Metrics to Implement:**

1. **Authorization Metrics:**
   - `raja.package_grant.enforce.count` (tags: decision=allow/deny, reason)
   - `raja.translation_grant.enforce.count` (tags: decision=allow/deny, reason)
   - `raja.enforce.latency` (histogram, tags: grant_type=path/package/translation)

2. **Manifest Resolution Metrics:**
   - `raja.manifest.resolve.count` (tags: cache_hit=true/false)
   - `raja.manifest.resolve.latency` (histogram, tags: cache_hit=true/false)
   - `raja.manifest.size` (histogram, bytes)
   - `raja.manifest.file_count` (histogram)

3. **Error Metrics:**
   - `raja.error.count` (tags: error_type, grant_type)
   - `raja.token.validation.failed.count` (tags: reason)

4. **Cache Metrics:**
   - `raja.cache.hit.count`
   - `raja.cache.miss.count`
   - `raja.cache.eviction.count` (if using LRU)
   - `raja.cache.size` (gauge, bytes)

**Distributed Tracing:**

- Integrate AWS X-Ray or OpenTelemetry
- Trace spans:
  - `enforce_package_grant` (root)
  - `validate_token`
  - `resolve_manifest` (with cache hit/miss attribute)
  - `check_membership`
  - `translate_logical_key`

### Error Alerting and Debugging Tools

**What's Needed:**

- Alerts for high error rates in package enforcement
- Debugging tools to inspect token claims
- Tools to validate quilt_uri format
- Tools to test manifest resolution

**Current State:**

- Token introspection via decode_token() (token.py:285-309)
- No CLI tools for package grant debugging
- No admin interface for viewing package grants

**Recommendation:** Add admin/debugging tools for production support

**CLI Tools to Implement:**

1. **Token Inspector:**
   ```bash
   raja token inspect <jwt>
   # Output: token type (RAJ-path/RAJ-package/TAJ-package), claims, expiration
   ```

2. **URI Validator:**
   ```bash
   raja uri validate <quilt_uri>
   # Output: parsed components, validation status, immutability check
   ```

3. **Manifest Resolver:**
   ```bash
   raja manifest resolve <quilt_uri>
   # Output: list of (bucket, key) tuples, file count, total size
   ```

4. **Authorization Simulator:**
   ```bash
   raja authz simulate --token <jwt> --bucket <bucket> --key <key> --action <action>
   # Output: allow/deny decision, reason, matched scope/package
   ```

**Admin Interface:**

- Web UI to view:
  - Active package grants by principal
  - Package resolution cache statistics
  - Authorization decision logs
  - Error rates and trends

### Deployment and Rollback Guidance

**What's Needed:**

- How to deploy package grant feature incrementally
- How to rollback if issues found
- Feature flag or toggle mechanism
- Migration path from path grants to package grants

**Current State:**

- No deployment docs specific to package grants
- No feature flag mechanism apparent

**Recommendation:** Document deployment strategy when ready for production

**Deployment Strategy:**

1. **Phase 1: Infrastructure Setup**
   - Deploy Cedar schema extension
   - Deploy manifest resolution Lambda
   - Deploy cache layer (Redis/DynamoDB)
   - No impact on existing path grants

2. **Phase 2: Soft Launch**
   - Enable package grant token issuance API
   - Feature flag: `ENABLE_PACKAGE_GRANTS=true` (default: false)
   - Test with internal users only
   - Monitor metrics and errors

3. **Phase 3: Gradual Rollout**
   - Enable for select customers
   - Monitor performance and errors
   - Adjust cache sizing if needed

4. **Phase 4: General Availability**
   - Enable for all users
   - Remove feature flag
   - Document migration path from path grants

**Rollback Plan:**

1. **Immediate Rollback:**
   - Set feature flag: `ENABLE_PACKAGE_GRANTS=false`
   - Existing path grants continue working
   - Package grant requests return error

2. **Full Rollback:**
   - Revert Cedar schema changes
   - Redeploy previous Lambda versions
   - Clear cache (if corrupted)

**Feature Flag Implementation:**

```python
# In enforcer.py
def enforce_with_routing(token_str: str, request: Request, secret: str) -> Decision:
    if not feature_flags.is_enabled("package_grants"):
        # Fall back to path grants only
        return enforce(token_str, request, secret)

    # Route based on token type
    token_type = detect_token_type(token_str)
    if token_type == "path":
        return enforce(token_str, request, secret)
    elif token_type == "package":
        return enforce_package_grant(token_str, request, secret, membership_checker)
    elif token_type == "translation":
        return enforce_translation_grant(token_str, request, secret, manifest_resolver)
```

---

## Conclusion

These hardening and optimization items should be addressed after the core functionality (manifest resolution, token routing, wildcard support) is complete and tested. Prioritize based on:

1. **Security hardening** - Before production launch
2. **Performance optimization** - When scale testing reveals bottlenecks
3. **Operational tooling** - As needed for production support

**Estimated Timeline:**

- Security hardening: 1 week
- Performance optimization: 1-2 weeks (depends on caching implementation)
- Operational tooling: Ongoing (add as needed)
