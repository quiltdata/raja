"""Tests for Cedar schema validation (Phase 2)."""

import os
import shutil

import pytest

from raja.cedar.schema import (
    CedarSchema,
    load_cedar_schema,
    validate_policy_against_schema,
)
from raja.models import CedarPolicy


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_VALIDATE_BIN"))


pytestmark = pytest.mark.skipif(
    not _cedar_tool_available(),
    reason="cargo or CEDAR_VALIDATE_BIN is required for schema validation",
)


def test_load_cedar_schema(tmp_path):
    """Test loading Cedar schema from file."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User {}
    entity Document {}

    action "read" appliesTo {
        principal: [User],
        resource: [Document]
    };
    """)

    schema = load_cedar_schema(str(schema_path), validate=False)

    assert "User" in schema.resource_types
    assert "Document" in schema.resource_types
    assert "read" in schema.actions


def test_schema_validates_resource_type():
    """Test schema validation catches unknown resource types."""
    schema = CedarSchema(resource_types={"Document", "File"}, actions={"read", "write"})

    policy = CedarPolicy(
        id="test",
        effect="permit",
        principal='User::"alice"',
        action='Action::"read"',
        resource='Unknown::"doc123"',
        resource_type="Unknown",
    )

    with pytest.raises(ValueError, match="unknown resource type: Unknown"):
        schema.validate_policy(policy)


def test_schema_validates_action():
    """Test schema validation catches unknown actions."""
    schema = CedarSchema(resource_types={"Document"}, actions={"read"})

    policy = CedarPolicy(
        id="test",
        effect="permit",
        principal='User::"alice"',
        action='Action::"write"',
        resource='Document::"doc123"',
        resource_type="Document",
    )

    with pytest.raises(ValueError, match="unknown action: write"):
        schema.validate_policy(policy)


def test_schema_validates_principal_type():
    """Test schema validation catches unknown principal types."""
    schema = CedarSchema(
        resource_types={"Document"},
        actions={"read"},
        principal_types={"User", "Group"},
    )

    policy = CedarPolicy(
        id="test",
        effect="permit",
        principal='Admin::"alice"',
        action='Action::"read"',
        resource='Document::"doc123"',
        resource_type="Document",
    )

    with pytest.raises(ValueError, match="unknown principal type: Admin"):
        schema.validate_policy(policy)


def test_schema_validates_action_resource_constraint():
    """Test schema validation catches action-resource mismatches."""
    schema = CedarSchema(
        resource_types={"Document", "File"},
        actions={"read"},
        action_constraints={"read": {"resourceTypes": ["Document"]}},
    )

    # This should fail: 'read' action cannot apply to 'File' resource
    policy = CedarPolicy(
        id="test",
        effect="permit",
        principal='User::"alice"',
        action='Action::"read"',
        resource='File::"file123"',
        resource_type="File",
    )

    with pytest.raises(ValueError, match="action read cannot be applied to resource type File"):
        schema.validate_policy(policy)


def test_schema_allows_valid_policy():
    """Test that valid policy passes schema validation."""
    schema = CedarSchema(
        resource_types={"Document"},
        actions={"read"},
        principal_types={"User"},
        action_constraints={"read": {"resourceTypes": ["Document"]}},
    )

    policy = CedarPolicy(
        id="test",
        effect="permit",
        principal='User::"alice"',
        action='Action::"read"',
        resource='Document::"doc123"',
        resource_type="Document",
    )

    # Should not raise
    schema.validate_policy(policy)


def test_validate_policy_against_schema_valid(tmp_path):
    """Test policy validation against schema file."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User;
    entity S3Bucket;

    action "s3:ListBucket" appliesTo {
        principal: [User],
        resource: [S3Bucket]
    };
    """)

    policy = """
    permit(
        principal == User::"alice",
        action == Action::"s3:ListBucket",
        resource == S3Bucket::"my-bucket"
    );
    """

    # Should not raise
    validate_policy_against_schema(policy, str(schema_path), use_cedar_cli=True)


def test_validate_policy_against_schema_invalid(tmp_path):
    """Test policy validation rejects schema violations."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User;
    entity Document;

    action "read" appliesTo {
        principal: [User],
        resource: [Document]
    };
    """)

    # Policy references unknown action
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"write",
        resource == Document::"doc123"
    );
    """

    with pytest.raises(ValueError, match="Cedar policy validation failed"):
        validate_policy_against_schema(policy, str(schema_path), use_cedar_cli=True)


def test_load_schema_with_hierarchies(tmp_path):
    """Test loading schema with entity hierarchies."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity S3Bucket {}
    entity S3Object in [S3Bucket] {}

    action "s3:GetObject" appliesTo {
        principal: [User],
        resource: [S3Object]
    };

    action "s3:ListBucket" appliesTo {
        principal: [User],
        resource: [S3Bucket]
    };
    """)

    schema = load_cedar_schema(str(schema_path), validate=False)

    assert "S3Bucket" in schema.resource_types
    assert "S3Object" in schema.resource_types
    assert "s3:GetObject" in schema.actions
    assert "s3:ListBucket" in schema.actions


def test_load_schema_with_multiple_principal_types(tmp_path):
    """Test loading schema with multiple principal types."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User {}
    entity Group {}
    entity Document {}

    action "read" appliesTo {
        principal: [User, Group],
        resource: [Document]
    };
    """)

    schema = load_cedar_schema(str(schema_path), validate=False)

    assert schema.principal_types is not None
    assert "User" in schema.principal_types
    assert "Group" in schema.principal_types


def test_schema_validation_with_cli(tmp_path):
    """Test schema validation using Cedar CLI."""
    schema_path = tmp_path / "schema.cedar"
    schema_path.write_text("""
    entity User;
    entity Document;

    action "read" appliesTo {
        principal: [User],
        resource: [Document]
    };
    """)

    # Should not raise
    schema = load_cedar_schema(str(schema_path), validate=True)
    assert schema is not None


def test_schema_validation_syntax_error(tmp_path):
    """Test that schema syntax errors are caught."""
    schema_path = tmp_path / "schema.cedar"
    # Invalid schema syntax
    schema_path.write_text("""
    entity User {
    entity Document {}
    """)

    with pytest.raises(ValueError, match="Cedar schema validation failed"):
        load_cedar_schema(str(schema_path), validate=True)
