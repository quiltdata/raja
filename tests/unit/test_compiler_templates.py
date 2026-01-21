"""Tests for policy template instantiation (Phase 4)."""

import os
import shutil

import pytest

from raja.compiler import instantiate_policy_template


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_PARSE_BIN"))


pytestmark = pytest.mark.skipif(
    not _cedar_tool_available(), reason="cargo or CEDAR_PARSE_BIN is required for Cedar parsing"
)


def test_instantiate_simple_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test instantiating a simple policy template."""
    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"{{action}}",
        resource == S3Bucket::"{{bucket}}"
    );
    """

    variables = {"user": "alice", "action": "s3:ListBucket", "bucket": "my-bucket"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "alice" in result
    assert "S3Bucket:my-bucket:s3:ListBucket" in result["alice"]


def test_instantiate_template_with_s3object(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test instantiating template with S3Object resource."""
    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"s3:GetObject",
        resource == S3Object::"report.csv"
    ) when {
        resource in S3Bucket::"{{bucket}}"
    };
    """

    variables = {"user": "bob", "bucket": "analytics-data"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "bob" in result
    assert "S3Object:analytics-data/report.csv:s3:GetObject" in result["bob"]


def test_instantiate_template_multiple_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test instantiating template with multiple actions."""
    template = """
    permit(
        principal == User::"{{user}}",
        action in [Action::"s3:GetObject", Action::"s3:PutObject"],
        resource == S3Object::"data.csv"
    ) when {
        resource in S3Bucket::"{{bucket}}"
    };
    """

    variables = {"user": "charlie", "bucket": "my-bucket"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "charlie" in result
    assert "S3Object:my-bucket/data.csv:s3:GetObject" in result["charlie"]
    assert "S3Object:my-bucket/data.csv:s3:PutObject" in result["charlie"]


def test_instantiate_template_missing_variable() -> None:
    """Test that missing variables raise error."""
    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"read",
        resource == Document::"{{document}}"
    );
    """

    variables = {"user": "alice"}  # Missing 'document'

    with pytest.raises(ValueError, match="unresolved template variables: document"):
        instantiate_policy_template(template, variables)


def test_instantiate_template_all_variables() -> None:
    """Test template with all supported variable types."""
    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"{{action}}",
        resource == Document::"{{resource}}"
    );
    """

    variables = {"user": "alice", "action": "read", "resource": "doc123"}

    result = instantiate_policy_template(template, variables)

    assert "alice" in result
    assert "Document:doc123:read" in result["alice"]


def test_instantiate_template_with_principal_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test template using 'principal' variable name."""
    template = """
    permit(
        principal == User::"{{principal}}",
        action == Action::"s3:ListBucket",
        resource == S3Bucket::"{{bucket}}"
    );
    """

    variables = {"principal": "admin", "bucket": "admin-bucket"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "admin" in result
    assert "S3Bucket:admin-bucket:s3:ListBucket" in result["admin"]


def test_instantiate_template_preserves_policy_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that template instantiation preserves policy structure."""
    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"s3:GetObject",
        resource == S3Object::"{{key}}"
    ) when {
        resource in S3Bucket::"{{bucket}}"
    };
    """

    variables = {"user": "alice", "key": "data/report.csv", "bucket": "my-bucket"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "alice" in result
    assert "S3Object:my-bucket/data/report.csv:s3:GetObject" in result["alice"]


def test_instantiate_template_no_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that template without variables works as normal policy."""
    template = """
    permit(
        principal == User::"alice",
        action == Action::"s3:ListBucket",
        resource == S3Bucket::"my-bucket"
    );
    """

    variables = {}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    result = instantiate_policy_template(template, variables)

    assert "alice" in result
    assert "S3Bucket:my-bucket:s3:ListBucket" in result["alice"]


def test_instantiate_template_alphanumeric_variables() -> None:
    """Test template with alphanumeric variable names."""
    template = """
    permit(
        principal == User::"{{user1}}",
        action == Action::"read",
        resource == Document::"{{doc_v2}}"
    );
    """

    variables = {"user1": "alice", "doc_v2": "report"}

    result = instantiate_policy_template(template, variables)

    assert "alice" in result
    assert "Document:report:read" in result["alice"]


def test_instantiate_template_with_schema_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Test template instantiation with schema validation."""
    # Create a simple schema file
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User {}
    entity S3Bucket {}

    action "s3:ListBucket" appliesTo {
        principal: [User],
        resource: [S3Bucket]
    };
    """)

    template = """
    permit(
        principal == User::"{{user}}",
        action == Action::"s3:ListBucket",
        resource == S3Bucket::"{{bucket}}"
    );
    """

    variables = {"user": "alice", "bucket": "my-bucket"}

    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")

    # Should work with valid schema
    result = instantiate_policy_template(template, variables, schema_path=str(schema_path))
    assert "alice" in result
