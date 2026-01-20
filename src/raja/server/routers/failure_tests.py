"""Failure mode testing APIs for the admin UI."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

import jwt
from fastapi import APIRouter, Depends, HTTPException

from raja.server import dependencies
from raja.server.logging_config import get_logger
from raja.server.routers.harness import (
    S3VerifyRequest,
    _harness_audience,
    _harness_issuer,
    _secret_kid,
    s3_harness_verify,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/failure-tests", tags=["failure-tests"])


class FailureTestStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class FailureTestDefinition:
    id: str
    title: str
    description: str
    category: str
    priority: str
    expected_summary: str
    setup: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "priority": self.priority,
            "expected_summary": self.expected_summary,
            "setup": self.setup,
        }


@dataclass
class FailureTestRun:
    run_id: str
    test_id: str
    status: str
    expected: str
    actual: str
    details: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "test_id": self.test_id,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class CategoryMeta(TypedDict):
    """Metadata for a failure test category."""

    label: str
    priority: str
    color: str
    description: str
    order: int


CATEGORY_META: dict[str, CategoryMeta] = {
    "token-security": {
        "label": "Token Security",
        "priority": "CRITICAL",
        "color": "#b72d2c",
        "description": "Tokens should never validate when expired, malformed, or tampered with.",
        "order": 0,
    },
    "cedar-compilation": {
        "label": "Cedar Policy Compilation",
        "priority": "CRITICAL",
        "color": "#b72d2c",
        "description": "Cedar policies must compile to correct scopes without silent failures.",
        "order": 1,
    },
    "scope-enforcement": {
        "label": "Scope Enforcement",
        "priority": "CRITICAL",
        "color": "#b72d2c",
        "description": "Scope matching must be exact and prevent prefix/substring attacks.",
        "order": 2,
    },
    "request-parsing": {
        "label": "Request Parsing",
        "priority": "HIGH",
        "color": "#c96f2b",
        "description": "S3 request parsing must handle malformed inputs safely.",
        "order": 3,
    },
    "cross-component": {
        "label": "Cross-Component",
        "priority": "CRITICAL",
        "color": "#b72d2c",
        "description": "Data flow between components must maintain consistency.",
        "order": 4,
    },
    "operational": {
        "label": "Operational",
        "priority": "HIGH",
        "color": "#c96f2b",
        "description": "System must handle operational scenarios correctly.",
        "order": 5,
    },
}

FAILURE_TEST_DEFINITIONS: list[FailureTestDefinition] = [
    FailureTestDefinition(
        id="1.1",
        title="Expired Token",
        description="Verification must fail when the JWT is expired.",
        category="token-security",
        priority="CRITICAL",
        expected_summary="DENY – token expired",
        setup="Mint a token, backdate the exp claim, and verify.",
    ),
    FailureTestDefinition(
        id="1.2",
        title="Invalid Signature",
        description="Tokens signed with the wrong secret must be rejected.",
        category="token-security",
        priority="CRITICAL",
        expected_summary="DENY – invalid signature",
        setup="Tamper with a valid token signature before verification.",
    ),
    FailureTestDefinition(
        id="1.3",
        title="Malformed JWT",
        description="Malformed tokens should not crash the verifier and must be rejected.",
        category="token-security",
        priority="CRITICAL",
        expected_summary="DENY – malformed token",
        setup="Send a token that does not follow the JWT format.",
    ),
    FailureTestDefinition(
        id="1.4",
        title="Missing/Empty Scopes",
        description="Tokens missing scope claims must be rejected.",
        category="token-security",
        priority="HIGH",
        expected_summary="DENY – missing scopes",
        setup="Mint tokens without the s3 scope and verify enforcement.",
    ),
    FailureTestDefinition(
        id="1.5",
        title="Token Claim Validation",
        description="Tokens with corrupted claim types must be rejected.",
        category="token-security",
        priority="HIGH",
        expected_summary="DENY – claim validation",
        setup="Corrupt sub/aud claims and expect verify to fail.",
    ),
    FailureTestDefinition(
        id="1.6",
        title="Token Revocation",
        description="Revoked tokens must no longer be accepted.",
        category="token-security",
        priority="MEDIUM",
        expected_summary="DENY – revoked token",
        setup="Simulate revocation and ensure verification fails.",
    ),
    # Cedar Compilation Failures
    FailureTestDefinition(
        id="2.1",
        title="Forbid Policies",
        description="Forbid policies must be compiled and enforced correctly.",
        category="cedar-compilation",
        priority="CRITICAL",
        expected_summary="DENY – forbid policy blocks access",
        setup="Create forbid policy and verify denial takes precedence.",
    ),
    FailureTestDefinition(
        id="2.2",
        title="Policy Syntax Errors",
        description="Malformed Cedar policies must be rejected during compilation.",
        category="cedar-compilation",
        priority="CRITICAL",
        expected_summary="ERROR – invalid policy syntax",
        setup="Submit policy with syntax errors to compiler.",
    ),
    FailureTestDefinition(
        id="2.3",
        title="Conflicting Policies",
        description="Multiple policies for same resource must resolve correctly.",
        category="cedar-compilation",
        priority="HIGH",
        expected_summary="Consistent resolution of conflicting policies",
        setup="Create overlapping permit/forbid and verify precedence.",
    ),
    FailureTestDefinition(
        id="2.4",
        title="Wildcard Expansion",
        description="Wildcard patterns in policies must expand correctly.",
        category="cedar-compilation",
        priority="HIGH",
        expected_summary="Wildcards expand to correct scope set",
        setup="Use wildcards in resource patterns and verify expansion.",
    ),
    FailureTestDefinition(
        id="2.5",
        title="Template Variables",
        description="Policy templates with variables must instantiate correctly.",
        category="cedar-compilation",
        priority="HIGH",
        expected_summary="Template variables resolve correctly",
        setup="Use policy templates and verify variable substitution.",
    ),
    FailureTestDefinition(
        id="2.6",
        title="Principal-Action Mismatch",
        description="Policies referencing non-existent principals/actions must fail.",
        category="cedar-compilation",
        priority="MEDIUM",
        expected_summary="ERROR – invalid principal or action reference",
        setup="Reference undefined entities in policy.",
    ),
    FailureTestDefinition(
        id="2.7",
        title="Schema Validation",
        description="Policies violating schema constraints must be rejected.",
        category="cedar-compilation",
        priority="MEDIUM",
        expected_summary="ERROR – schema violation",
        setup="Create policy that violates schema rules.",
    ),
    # Scope Enforcement Failures
    FailureTestDefinition(
        id="3.1",
        title="Prefix Attacks",
        description="Scope matching must prevent prefix-based authorization bypass.",
        category="scope-enforcement",
        priority="CRITICAL",
        expected_summary="DENY – prefix attack blocked",
        setup="Request bucket123 with bucket12 scope.",
    ),
    FailureTestDefinition(
        id="3.2",
        title="Substring Attacks",
        description="Partial matches in resource IDs must be denied.",
        category="scope-enforcement",
        priority="CRITICAL",
        expected_summary="DENY – substring attack blocked",
        setup="Request with scope containing resource as substring.",
    ),
    FailureTestDefinition(
        id="3.3",
        title="Case Sensitivity",
        description="Resource matching must be case-sensitive.",
        category="scope-enforcement",
        priority="HIGH",
        expected_summary="DENY – case mismatch",
        setup="Request BUCKET with bucket scope.",
    ),
    FailureTestDefinition(
        id="3.4",
        title="Action Specificity",
        description="Broad action scopes should not grant narrow permissions.",
        category="scope-enforcement",
        priority="HIGH",
        expected_summary="DENY – action mismatch",
        setup="Request s3:PutObject with only s3:GetObject scope.",
    ),
    FailureTestDefinition(
        id="3.5",
        title="Wildcard Boundaries",
        description="Wildcard scopes must respect component boundaries.",
        category="scope-enforcement",
        priority="MEDIUM",
        expected_summary="Wildcards match within boundaries only",
        setup="Verify bucket:* doesn't match bucket-admin.",
    ),
    FailureTestDefinition(
        id="3.6",
        title="Scope Ordering",
        description="Scope evaluation order must not affect decisions.",
        category="scope-enforcement",
        priority="MEDIUM",
        expected_summary="Consistent evaluation regardless of order",
        setup="Test same scopes in different orders.",
    ),
    FailureTestDefinition(
        id="3.7",
        title="Empty Scope Handling",
        description="Empty or null scope arrays must deny all access.",
        category="scope-enforcement",
        priority="MEDIUM",
        expected_summary="DENY – empty scopes",
        setup="Send request with no scopes in token.",
    ),
    FailureTestDefinition(
        id="3.8",
        title="Malformed Scope Format",
        description="Invalid scope strings must be rejected safely.",
        category="scope-enforcement",
        priority="LOW",
        expected_summary="DENY – invalid scope format",
        setup="Include malformed scopes in token claims.",
    ),
    # Request Parsing Failures
    FailureTestDefinition(
        id="4.1",
        title="Missing Required Headers",
        description="Requests without Authorization header must be denied.",
        category="request-parsing",
        priority="CRITICAL",
        expected_summary="DENY – missing authorization",
        setup="Send S3 request without Authorization header.",
    ),
    FailureTestDefinition(
        id="4.2",
        title="Malformed S3 Requests",
        description="Invalid S3 request format must be rejected safely.",
        category="request-parsing",
        priority="HIGH",
        expected_summary="ERROR – malformed request",
        setup="Send requests with invalid HTTP structure.",
    ),
    FailureTestDefinition(
        id="4.3",
        title="Path Traversal",
        description="Path traversal attacks in keys must be blocked.",
        category="request-parsing",
        priority="HIGH",
        expected_summary="DENY – path traversal blocked",
        setup="Request keys containing ../ sequences.",
    ),
    FailureTestDefinition(
        id="4.4",
        title="URL Encoding Edge Cases",
        description="Unusual URL encoding must be handled correctly.",
        category="request-parsing",
        priority="MEDIUM",
        expected_summary="Correctly decode and match resources",
        setup="Use double-encoded or unusual encodings in keys.",
    ),
    FailureTestDefinition(
        id="4.5",
        title="HTTP Method Mapping",
        description="All S3 HTTP methods must map to correct actions.",
        category="request-parsing",
        priority="MEDIUM",
        expected_summary="Correct action derived from method",
        setup="Test GET/PUT/DELETE/HEAD methods.",
    ),
    # Cross-Component Failures
    FailureTestDefinition(
        id="5.1",
        title="Compiler-Enforcer Sync",
        description="Policy changes must propagate to enforcement layer.",
        category="cross-component",
        priority="CRITICAL",
        expected_summary="Enforcement reflects latest compiled policies",
        setup="Update policy and verify immediate enforcement.",
    ),
    FailureTestDefinition(
        id="5.2",
        title="Token-Scope Consistency",
        description="Tokens must contain exactly the scopes from compilation.",
        category="cross-component",
        priority="CRITICAL",
        expected_summary="Token scopes match compiled policy",
        setup="Issue token and verify scope claim matches compilation.",
    ),
    FailureTestDefinition(
        id="5.3",
        title="Schema-Policy Consistency",
        description="Policy store schema must align with enforcement logic.",
        category="cross-component",
        priority="CRITICAL",
        expected_summary="Schema entities match enforcement expectations",
        setup="Verify resource types in schema match enforcer.",
    ),
    FailureTestDefinition(
        id="5.4",
        title="DynamoDB Lag",
        description="Eventually consistent reads must not cause authorization gaps.",
        category="cross-component",
        priority="HIGH",
        expected_summary="No authorization bypass due to replication lag",
        setup="Update policy and immediately issue token.",
    ),
    FailureTestDefinition(
        id="5.5",
        title="JWT Claims Structure",
        description="Token claims must follow expected structure for Lua enforcer.",
        category="cross-component",
        priority="HIGH",
        expected_summary="Lua enforcer correctly parses JWT claims",
        setup="Issue token and verify Envoy Lua can parse it.",
    ),
    FailureTestDefinition(
        id="5.6",
        title="Policy ID Tracking",
        description="Policy updates must maintain correct version tracking.",
        category="cross-component",
        priority="MEDIUM",
        expected_summary="Policy version changes tracked correctly",
        setup="Update policy and verify version increment.",
    ),
    # Operational Failures
    FailureTestDefinition(
        id="6.1",
        title="Secrets Rotation",
        description="JWT secret rotation must not break active tokens.",
        category="operational",
        priority="HIGH",
        expected_summary="Graceful secret rotation with overlap",
        setup="Rotate secret while tokens are in use.",
    ),
    FailureTestDefinition(
        id="6.2",
        title="Clock Skew",
        description="System must handle reasonable clock drift between services.",
        category="operational",
        priority="HIGH",
        expected_summary="Tolerate clock skew within bounds",
        setup="Test with skewed system clocks.",
    ),
    FailureTestDefinition(
        id="6.3",
        title="Rate Limiting",
        description="Excessive authorization requests must be rate-limited.",
        category="operational",
        priority="MEDIUM",
        expected_summary="Rate limiting enforced correctly",
        setup="Send burst of authorization requests.",
    ),
    FailureTestDefinition(
        id="6.4",
        title="Large Token Payloads",
        description="Tokens with many scopes must stay within size limits.",
        category="operational",
        priority="MEDIUM",
        expected_summary="Token size within HTTP header limits",
        setup="Issue token with hundreds of scopes.",
    ),
    FailureTestDefinition(
        id="6.5",
        title="Policy Store Unavailability",
        description="Authorization must fail closed when AVP is unreachable.",
        category="operational",
        priority="MEDIUM",
        expected_summary="DENY when policy store unavailable",
        setup="Simulate AVP service disruption.",
    ),
    FailureTestDefinition(
        id="6.6",
        title="Logging Sensitive Data",
        description="Logs must not contain token secrets or sensitive claims.",
        category="operational",
        priority="LOW",
        expected_summary="Sensitive data redacted from logs",
        setup="Trigger errors and inspect logs.",
    ),
    FailureTestDefinition(
        id="6.7",
        title="Metrics Collection",
        description="Authorization decisions must be recorded in metrics.",
        category="operational",
        priority="LOW",
        expected_summary="Metrics reflect authorization activity",
        setup="Make requests and verify metric updates.",
    ),
]

FAILURE_TEST_BY_ID: dict[str, FailureTestDefinition] = {
    test.id: test for test in FAILURE_TEST_DEFINITIONS
}

RUN_HISTORY: dict[str, FailureTestRun] = {}

DEFAULT_RESOURCE = {"bucket": "raja-failure-token", "key": "edge-case/object"}
DEFAULT_ACTION = "s3:GetObject"


def _build_token(secret: str, exp_offset: int) -> str:
    issued_at = int(time.time())
    payload = {
        "iss": _harness_issuer(),
        "sub": "User::failure-mode",
        "aud": _harness_audience(),
        "iat": issued_at,
        "exp": issued_at + exp_offset,
        "action": DEFAULT_ACTION,
        "s3": DEFAULT_RESOURCE,
    }
    headers = {"kid": _secret_kid(secret), "typ": "RAJ"}
    return jwt.encode(payload, secret, algorithm="HS256", headers=headers)


def _tamper_signature(token: str) -> str:
    if token[-1] != "A":
        return token[:-1] + "A"
    return token[:-1] + "B"


def _verify_token(token: str, secret: str) -> dict[str, Any]:
    request = S3VerifyRequest(token=token)
    return s3_harness_verify(request, secret=secret)


def _runner_expired(secret: str) -> FailureTestRun:
    token = _build_token(secret, exp_offset=-60)
    response = _verify_token(token, secret)
    error = response.get("error", "unknown").lower()
    invalid = not response.get("valid")
    expired = "expired" in error
    status = FailureTestStatus.PASS if invalid and expired else FailureTestStatus.FAIL
    return FailureTestRun(
        run_id="",
        test_id="1.1",
        status=status,
        expected="DENY – token expired",
        actual=response.get("error", "Unexpected response"),
        details={"response": response},
        timestamp=time.time(),
    )


def _runner_invalid_signature(secret: str) -> FailureTestRun:
    token = _build_token(secret, exp_offset=600)
    tampered = _tamper_signature(token)
    response = _verify_token(tampered, secret)
    error = response.get("error", "").lower()
    invalid = not response.get("valid")
    signature_error = "invalid token" in error
    status = FailureTestStatus.PASS if invalid and signature_error else FailureTestStatus.FAIL
    return FailureTestRun(
        run_id="",
        test_id="1.2",
        status=status,
        expected="DENY – invalid signature",
        actual=response.get("error", "Unexpected response"),
        details={"response": response},
        timestamp=time.time(),
    )


def _runner_malformed(secret: str) -> FailureTestRun:
    response = _verify_token("not.a.jwt", secret)
    error = response.get("error", "").lower()
    invalid = not response.get("valid")
    malformed = "invalid token" in error
    status = FailureTestStatus.PASS if invalid and malformed else FailureTestStatus.FAIL
    return FailureTestRun(
        run_id="",
        test_id="1.3",
        status=status,
        expected="DENY – malformed token",
        actual=response.get("error", "Unexpected response"),
        details={"response": response},
        timestamp=time.time(),
    )


def _runner_missing_scopes(secret: str) -> FailureTestRun:
    """Test token with missing or empty scopes claim."""
    issued_at = int(time.time())
    # Create token without s3 scope claim
    payload = {
        "iss": _harness_issuer(),
        "sub": "User::failure-mode",
        "aud": _harness_audience(),
        "iat": issued_at,
        "exp": issued_at + 600,
        "action": DEFAULT_ACTION,
        # Note: no "s3" claim
    }
    headers = {"kid": _secret_kid(secret), "typ": "RAJ"}
    token = jwt.encode(payload, secret, algorithm="HS256", headers=headers)
    response = _verify_token(token, secret)

    error = response.get("error", "").lower()
    invalid = not response.get("valid")
    missing_scope = "missing" in error or "scope" in error
    status = FailureTestStatus.PASS if invalid and missing_scope else FailureTestStatus.FAIL
    return FailureTestRun(
        run_id="",
        test_id="1.4",
        status=status,
        expected="DENY – missing scopes",
        actual=response.get("error", "Unexpected response"),
        details={"response": response},
        timestamp=time.time(),
    )


def _runner_claim_validation(secret: str) -> FailureTestRun:
    """Test token with corrupted claim types."""
    issued_at = int(time.time())
    # Create token with wrong claim types
    payload = {
        "iss": _harness_issuer(),
        "sub": 12345,  # Should be string, not int
        "aud": ["wrong-audience"],  # Wrong audience
        "iat": issued_at,
        "exp": issued_at + 600,
        "action": DEFAULT_ACTION,
        "s3": DEFAULT_RESOURCE,
    }
    headers = {"kid": _secret_kid(secret), "typ": "RAJ"}
    token = jwt.encode(payload, secret, algorithm="HS256", headers=headers)
    response = _verify_token(token, secret)

    error = response.get("error", "").lower()
    invalid = not response.get("valid")
    claim_error = "claim" in error or "validation" in error or "invalid" in error
    status = FailureTestStatus.PASS if invalid and claim_error else FailureTestStatus.FAIL
    return FailureTestRun(
        run_id="",
        test_id="1.5",
        status=status,
        expected="DENY – claim validation",
        actual=response.get("error", "Unexpected response"),
        details={"response": response},
        timestamp=time.time(),
    )


def _runner_revocation(secret: str) -> FailureTestRun:
    """Test token revocation (not yet implemented)."""
    # Token revocation is not implemented yet, so this test should show NOT_IMPLEMENTED
    return FailureTestRun(
        run_id="",
        test_id="1.6",
        status=FailureTestStatus.NOT_IMPLEMENTED,
        expected="DENY – revoked token",
        actual="Token revocation feature not implemented",
        details={
            "note": "Revocation requires additional infrastructure (Redis/DynamoDB blacklist)"
        },
        timestamp=time.time(),
    )


RUNNERS: dict[str, Callable[[str], FailureTestRun]] = {
    "1.1": _runner_expired,
    "1.2": _runner_invalid_signature,
    "1.3": _runner_malformed,
    "1.4": _runner_missing_scopes,
    "1.5": _runner_claim_validation,
    "1.6": _runner_revocation,
}


def _store_run(run: FailureTestRun) -> FailureTestRun:
    run_with_id = FailureTestRun(
        run_id=str(uuid.uuid4()),
        test_id=run.test_id,
        status=run.status,
        expected=run.expected,
        actual=run.actual,
        details=run.details,
        timestamp=run.timestamp,
    )
    RUN_HISTORY[run_with_id.run_id] = run_with_id
    return run_with_id


def _execute_test(test_id: str, secret: str) -> FailureTestRun:
    definition = FAILURE_TEST_BY_ID.get(test_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Unknown failure test")
    runner = RUNNERS.get(test_id)
    if runner is None:
        base_run = FailureTestRun(
            run_id="",
            test_id=test_id,
            status=FailureTestStatus.NOT_IMPLEMENTED,
            expected=definition.expected_summary,
            actual="Runner not available",
            details={"error": "Runner not implemented for this test."},
            timestamp=time.time(),
        )
        return _store_run(base_run)
    try:
        run_result = runner(secret)
    except Exception as exc:  # pragma: no cover - best effort runner
        logger.exception("failure_test_runner_error", test_id=test_id, error=str(exc))
        error_run = FailureTestRun(
            run_id="",
            test_id=test_id,
            status=FailureTestStatus.ERROR,
            expected=definition.expected_summary,
            actual=str(exc),
            details={"error": str(exc)},
            timestamp=time.time(),
        )
        return _store_run(error_run)
    # Ensure expected summary matches definition even if runner sets different text
    run_result.expected = definition.expected_summary
    return _store_run(run_result)


def _serialize_test_definition(test: FailureTestDefinition) -> dict[str, Any]:
    data = test.to_dict()
    meta: CategoryMeta | None = CATEGORY_META.get(test.category)
    data["category_label"] = meta["label"] if meta else test.category
    data["color"] = meta["color"] if meta else None
    data["priority"] = test.priority
    return data


@router.get("/")
def list_failure_tests(secret: str = Depends(dependencies.get_harness_secret)) -> dict[str, Any]:
    tests = [_serialize_test_definition(test) for test in FAILURE_TEST_DEFINITIONS]
    categories = [
        {
            "id": cat_id,
            "label": meta["label"],
            "priority": meta["priority"],
            "color": meta["color"],
            "description": meta["description"],
            "tests": [test.id for test in FAILURE_TEST_DEFINITIONS if test.category == cat_id],
        }
        for cat_id, meta in sorted(CATEGORY_META.items(), key=lambda item: item[1]["order"])
    ]
    return {"tests": tests, "categories": categories}


@router.get("/{test_id}")
def get_failure_test_definition(
    test_id: str, secret: str = Depends(dependencies.get_harness_secret)
) -> dict[str, Any]:
    definition = FAILURE_TEST_BY_ID.get(test_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Failure test not found")
    return _serialize_test_definition(definition)


@router.post("/{test_id}/run")
def run_failure_test(
    test_id: str, secret: str = Depends(dependencies.get_harness_secret)
) -> dict[str, Any]:
    run = _execute_test(test_id, secret)
    return run.to_dict()


@router.post("/categories/{category}/run")
def run_failure_category(
    category: str, secret: str = Depends(dependencies.get_harness_secret)
) -> dict[str, Any]:
    if category not in CATEGORY_META:
        raise HTTPException(status_code=404, detail="Unknown failure category")
    results = []
    for test in FAILURE_TEST_DEFINITIONS:
        if test.category != category:
            continue
        results.append(_execute_test(test.id, secret).to_dict())
    return {"category": category, "results": results}


@router.get("/runs/{run_id}")
def get_failure_run(run_id: str) -> dict[str, Any]:
    run = RUN_HISTORY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@router.delete("/runs/{run_id}")
def delete_failure_run(run_id: str) -> dict[str, bool]:
    if run_id in RUN_HISTORY:
        del RUN_HISTORY[run_id]
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Run not found")
