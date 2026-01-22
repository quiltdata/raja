# Performance Measurement Specification

**Status:** Draft
**Created:** 2026-01-16
**Issue:** [#23](https://github.com/quiltdata/raja/issues/23)

## Overview

This spec defines the methodology and implementation plan for measuring the performance overhead of RAJEE's JWT+Lua authorization filter chain compared to the legacy unauthenticated baseline.

## Motivation

Before promoting RAJEE to production, we must quantify:

1. **Authorization overhead** - How much latency does JWT validation + Lua matching add?
2. **Scalability characteristics** - How does performance degrade with token complexity?
3. **Production readiness** - Is the overhead acceptable for real-world use?

## Goals

- Measure end-to-end latency impact of authorization
- Isolate pure authorization cost from S3 operation cost
- Understand performance scaling with grant count
- Establish baseline for regression testing
- Document findings for production readiness decision

## Non-Goals

- Optimize performance (this is measurement-focused)
- Production deployment (evaluation only)
- Load testing or stress testing (baseline performance only)

## Architecture

### Test Scenarios

We will implement three complementary test scenarios:

#### 1. A/B Test: Real S3 Operations

**Setup:**
- Same S3 operations (PUT/GET/DELETE) through both configurations
- Configuration A: Envoy with auth disabled (legacy mode)
- Configuration B: Envoy with JWT+Lua enabled

**Purpose:** Measure real-world overhead in production-like scenario

**Metrics:**
- End-to-end latency (client perspective)
- P50/P95/P99 percentiles
- Request throughput

**Limitation:** S3 latency dominates, making auth overhead appear small

#### 2. Isolated Overhead Test: Echo Server

**Setup:**
- Deploy minimal HTTP echo server as upstream
- Run with/without auth filters
- Identical request patterns in both modes

**Purpose:** Isolate pure authorization cost by removing S3 variability

**Metrics:**
- Filter processing time (from Envoy stats)
- CPU usage per request
- Memory consumption

**Benefit:** Shows true authorization overhead without S3 noise

#### 3. Scalability Test: Varying Grant Counts

**Setup:**
- Generate tokens with different grant counts:
  - **Baseline:** 6 grants (current test data)
  - **10x:** 60 grants
  - **100x:** 600 grants
  - **1000x:** 6000 grants (stress test)

**Purpose:** Understand how Lua matching performance scales

**Metrics:**
- Latency vs. grant count
- CPU usage vs. grant count
- Memory usage vs. grant count

**Decision point:** Identify if we need optimization (caching, native filter, etc.)

## Implementation Plan

### Phase 1: Infrastructure Setup

#### 1.1 Echo Server Deployment

Add a minimal echo server to the test infrastructure:

```python
# infra/raja_poc/constructs/echo_server.py
class EchoServer(Construct):
    """Minimal HTTP server that echoes requests back"""

    def __init__(self, scope, id, vpc, cluster):
        # Deploy Fargate task with httpbin or custom echo service
        # Register with Envoy as alternative upstream
        # No S3 dependencies
```

**Requirements:**
- Must respond quickly (<10ms)
- Should echo headers and body for verification
- Minimal resource usage (no heavy processing)

**Options:**
- Use existing httpbin Docker image
- Deploy custom Python/Go echo server
- Use AWS Lambda function URL

#### 1.2 Dual-Mode Envoy Configuration

Extend Envoy configuration to support auth toggle:

```python
# infra/raja_poc/constructs/envoy_proxy.py
class EnvoyProxy(Construct):
    def __init__(self, scope, id, auth_enabled: bool = True):
        # When auth_enabled=False, skip JWT filter and Lua filter
        # Keep routing logic identical
        # Allow runtime switching for A/B tests
```

**Implementation:**
- Use environment variable: `ENABLE_AUTH=true|false`
- Conditionally include filters in envoy.yaml template
- Ensure same routing for fair comparison

### Phase 2: Token Generation

#### 2.1 Variable Grant Token Generator

Create utility to generate test tokens with configurable grant counts:

```python
# scripts/generate_perf_tokens.py
def generate_performance_tokens(
    grant_counts: list[int],
    output_dir: Path
) -> dict[int, str]:
    """
    Generate JWT tokens with varying grant counts.

    Args:
        grant_counts: List of grant counts to generate (e.g., [6, 60, 600])
        output_dir: Directory to save tokens and metadata

    Returns:
        Mapping of grant_count -> token
    """
    # Use RAJA token service to generate tokens
    # Grants should be realistic (mix of resource types/actions)
    # Save tokens and metadata for reproducibility
```

**Grant Pattern:**
- Use realistic mix: Documents, Buckets, Objects
- Mix of wildcards: `*:*:read`, `Bucket:bucket1:*`, `Object:bucket1/key1:read`
- Maintain same principal across all tokens

**Output:**
- JSON file with tokens and metadata
- Human-readable summary
- SHA256 hashes for verification

### Phase 3: Measurement Harness

#### 3.1 Performance Test Framework

Create structured performance testing framework:

```python
# tests/performance/test_auth_overhead.py
class AuthOverheadTest:
    """Measures authorization overhead in various scenarios"""

    def test_s3_with_vs_without_auth(self):
        """A/B test with real S3 operations"""
        # Run same S3 operations through both Envoy configs
        # Collect latency distributions
        # Generate comparison report

    def test_echo_server_overhead(self):
        """Isolated auth overhead measurement"""
        # Run requests through echo server
        # Measure pure filter processing time
        # Calculate overhead percentage

    def test_scaling_with_grant_count(self):
        """Grant count scalability test"""
        # Iterate through different grant counts
        # Measure latency for each
        # Identify performance degradation points
```

**Test Utilities:**

```python
# tests/performance/utils.py
class PerformanceMetrics:
    """Collect and analyze performance metrics"""

    def __init__(self):
        self.samples: list[float] = []

    def record(self, latency_ms: float):
        self.samples.append(latency_ms)

    def percentile(self, p: int) -> float:
        """Calculate percentile (e.g., p=50, p=95, p=99)"""
        return np.percentile(self.samples, p)

    def mean(self) -> float:
        return np.mean(self.samples)

    def std(self) -> float:
        return np.std(self.samples)

    def overhead_vs(self, baseline: 'PerformanceMetrics') -> float:
        """Calculate overhead percentage vs baseline"""
        return ((self.mean() - baseline.mean()) / baseline.mean()) * 100
```

#### 3.2 Envoy Stats Collection

Extract relevant metrics from Envoy stats API:

```python
# tests/performance/envoy_stats.py
class EnvoyStatsCollector:
    """Collect performance stats from Envoy admin API"""

    def get_filter_timing(self, filter_name: str) -> dict:
        """
        Get timing stats for specific filter.

        Returns:
            {
                'mean_ms': float,
                'p50_ms': float,
                'p95_ms': float,
                'p99_ms': float
            }
        """
        # Query /stats/prometheus endpoint
        # Parse histogram data
        # Return structured metrics

    def get_cpu_usage(self) -> float:
        """Get CPU usage percentage"""
        # Query /stats endpoint for worker CPU time

    def get_memory_usage(self) -> int:
        """Get memory usage in bytes"""
        # Query /memory endpoint
```

### Phase 4: Analysis and Reporting

#### 4.1 Results Analysis

Automated analysis of performance data:

```python
# scripts/analyze_performance.py
class PerformanceAnalyzer:
    """Analyze and compare performance test results"""

    def compare_configurations(
        self,
        baseline: PerformanceMetrics,
        test: PerformanceMetrics
    ) -> dict:
        """
        Compare baseline vs test configuration.

        Returns:
            {
                'overhead_percent': float,
                'overhead_ms': float,
                'baseline_p95': float,
                'test_p95': float,
                'statistical_significance': bool
            }
        """
        # Calculate overhead
        # Run statistical tests (t-test, Mann-Whitney U)
        # Determine if difference is significant

    def analyze_scaling(
        self,
        results: dict[int, PerformanceMetrics]
    ) -> dict:
        """
        Analyze scaling characteristics.

        Args:
            results: Mapping of grant_count -> metrics

        Returns:
            {
                'scaling_factor': float,  # O(n), O(log n), etc.
                'acceptable_max_grants': int,
                'degradation_threshold': int
            }
        """
        # Fit performance curve
        # Identify inflection points
        # Recommend limits
```

#### 4.2 Report Generation

Generate comprehensive performance report:

```markdown
# Performance Report Template

## Summary

- **Test Date:** YYYY-MM-DD
- **Environment:** AWS Region, instance types
- **Baseline:** Envoy without auth
- **Test:** Envoy with JWT+Lua

## Key Findings

### End-to-End Overhead (S3 Operations)

| Metric | Baseline | With Auth | Overhead |
|--------|----------|-----------|----------|
| Mean   | X ms     | Y ms      | Z%       |
| P50    | X ms     | Y ms      | Z%       |
| P95    | X ms     | Y ms      | Z%       |
| P99    | X ms     | Y ms      | Z%       |

### Isolated Authorization Cost (Echo Server)

| Metric | No Auth | With Auth | Overhead |
|--------|---------|-----------|----------|
| Mean   | X ms    | Y ms      | Z ms     |
| P95    | X ms    | Y ms      | Z ms     |

### Scaling with Grant Count

| Grant Count | P95 Latency | Overhead vs 6 |
|-------------|-------------|---------------|
| 6           | X ms        | baseline      |
| 60          | Y ms        | +Z%           |
| 600         | Y ms        | +Z%           |
| 6000        | Y ms        | +Z%           |

## Analysis

### Production Readiness

- [ ] Overhead acceptable for production use (< X%)
- [ ] Scales to expected maximum grant counts
- [ ] No significant resource exhaustion
- [ ] Performance predictable and stable

### Recommendations

- Maximum recommended grant count: N
- Optimization needed: Yes/No
- Next steps: ...

## Detailed Results

[Graphs and detailed data]
```

## Metrics and Success Criteria

### Primary Metrics

1. **Authorization Overhead Percentage**
   - Target: < 10% for P95 latency
   - Measurement: (auth_latency - baseline_latency) / baseline_latency × 100

2. **Pure Filter Cost**
   - Target: < 5ms at P95
   - Measurement: Echo server latency with auth enabled

3. **Scaling Factor**
   - Target: O(n) or better for grant count
   - Measurement: Latency growth rate vs grant count

### Secondary Metrics

- CPU usage per request
- Memory consumption
- Request throughput (requests/sec)
- Error rates (should be 0%)

### Success Criteria

The authorization system is production-ready if:

- ✅ P95 overhead < 10% for typical S3 operations
- ✅ Pure filter cost < 5ms at P95
- ✅ Scales linearly to 600 grants
- ✅ No performance degradation over 1000 requests
- ✅ Stable memory usage (no leaks)
- ✅ Reproducible results across runs

## Implementation Sequence

1. **Week 1: Infrastructure**
   - Deploy echo server construct
   - Add auth toggle to Envoy configuration
   - Verify dual-mode operation

2. **Week 2: Token Generation**
   - Implement variable grant token generator
   - Generate test tokens for all scenarios
   - Validate token correctness

3. **Week 3: Measurement**
   - Build performance test framework
   - Implement Envoy stats collection
   - Run initial measurements

4. **Week 4: Analysis**
   - Analyze results
   - Generate performance report
   - Make production readiness decision

## Testing Plan

### Unit Tests

- Token generator produces correct grants
- Metrics calculation accuracy
- Statistical analysis correctness

### Integration Tests

- Echo server responds correctly
- Envoy dual-mode switching works
- Stats collection retrieves valid data

### Performance Tests

- All three test scenarios execute successfully
- Results are reproducible (CV < 5%)
- No test infrastructure overhead

## Risks and Mitigations

### Risk: S3 Variability Masks Auth Overhead

**Impact:** High
**Probability:** High
**Mitigation:** Use echo server for isolated measurement

### Risk: Network Latency Dominates

**Impact:** Medium
**Probability:** Medium
**Mitigation:** Deploy all components in same VPC/AZ

### Risk: Insufficient Sample Size

**Impact:** Medium
**Probability:** Low
**Mitigation:** Run 1000+ requests per scenario, calculate confidence intervals

### Risk: Cache Warming Effects

**Impact:** Medium
**Probability:** Medium
**Mitigation:** Implement proper warmup period, discard initial samples

## Future Considerations

### Performance Optimization Paths

If overhead exceeds targets:

1. **Caching:** Cache grant matching results
2. **Native Filter:** Implement in C++ instead of Lua
3. **Precompiled Patterns:** Compile wildcard patterns to regex
4. **Index Structures:** Use trie or hash map for grant lookup

### Continuous Performance Monitoring

- Add performance tests to CI pipeline
- Alert on regressions > 5%
- Track metrics over time

### Production Monitoring

- Emit auth filter timing metrics
- Create CloudWatch dashboard
- Set up alerting for degradation

## References

- [Issue #23](https://github.com/quiltdata/raja/issues/23) - Original performance measurement request
- [13-authorization-verification.md](./13-authorization-verification.md) - Current auth implementation
- Envoy performance best practices
- Lua filter performance guide

## Appendix: Test Data Format

### Token Metadata

```json
{
  "grant_count": 60,
  "token": "eyJ...",
  "grants": [
    "Document:doc1:read",
    "Bucket:*:list",
    ...
  ],
  "principal": "User::perf-test",
  "issued_at": "2026-01-16T00:00:00Z",
  "expires_at": "2026-01-17T00:00:00Z",
  "sha256": "abc123..."
}
```

### Raw Results

```json
{
  "scenario": "s3_with_auth",
  "timestamp": "2026-01-16T00:00:00Z",
  "samples": [12.3, 13.1, 11.8, ...],
  "envoy_stats": {
    "cpu_percent": 5.2,
    "memory_mb": 128,
    "filter_timing_ms": {
      "jwt": 1.2,
      "lua": 2.3
    }
  }
}
```

## Document History

- 2026-01-16: Initial draft based on issue #23
