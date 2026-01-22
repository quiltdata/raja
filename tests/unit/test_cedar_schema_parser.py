"""Tests for Cedar schema parsing to AVP JSON format."""

import json
from pathlib import Path

import pytest

from raja.cedar.schema import load_cedar_schema_from_file, parse_cedar_schema_to_avp_json


@pytest.mark.unit
def test_parse_simple_schema():
    """Test parsing a simple Cedar schema with entities and actions."""
    schema = """
    entity User {}
    entity Document {}

    action "read" appliesTo {
      principal: [User],
      resource: [Document]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    assert "Raja" in parsed
    assert "entityTypes" in parsed["Raja"]
    assert "actions" in parsed["Raja"]

    # Check entities
    assert "User" in parsed["Raja"]["entityTypes"]
    assert "Document" in parsed["Raja"]["entityTypes"]
    assert parsed["Raja"]["entityTypes"]["User"]["memberOfTypes"] == []
    assert parsed["Raja"]["entityTypes"]["Document"]["memberOfTypes"] == []

    # Check actions
    assert "read" in parsed["Raja"]["actions"]
    assert parsed["Raja"]["actions"]["read"]["appliesTo"]["principalTypes"] == ["User"]
    assert parsed["Raja"]["actions"]["read"]["appliesTo"]["resourceTypes"] == ["Document"]


@pytest.mark.unit
def test_parse_schema_with_entity_hierarchy():
    """Test parsing schema with entity hierarchies (in relationships)."""
    schema = """
    entity S3Bucket {}
    entity S3Object in [S3Bucket] {}

    action "s3:GetObject" appliesTo {
      principal: [User],
      resource: [S3Object]
    }

    entity User {}
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    # Check entity with parent
    assert "S3Object" in parsed["Raja"]["entityTypes"]
    assert parsed["Raja"]["entityTypes"]["S3Object"]["memberOfTypes"] == ["S3Bucket"]

    # Check entity without parent
    assert "S3Bucket" in parsed["Raja"]["entityTypes"]
    assert parsed["Raja"]["entityTypes"]["S3Bucket"]["memberOfTypes"] == []


@pytest.mark.unit
def test_parse_schema_with_multiple_principals_and_resources():
    """Test parsing actions with multiple principal and resource types."""
    schema = """
    entity User {}
    entity Role {}
    entity Document {}
    entity Report {}

    action "read" appliesTo {
      principal: [User, Role],
      resource: [Document, Report]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    action = parsed["Raja"]["actions"]["read"]
    assert set(action["appliesTo"]["principalTypes"]) == {"User", "Role"}
    assert set(action["appliesTo"]["resourceTypes"]) == {"Document", "Report"}


@pytest.mark.unit
def test_parse_schema_with_comments():
    """Test that comments are properly stripped from schema."""
    schema = """
    // Principals
    entity User {}
    entity Role {}

    // Resources
    entity Document {}  // Document entity

    // Actions
    action "read" appliesTo {
      principal: [User, Role],  // Allow both users and roles
      resource: [Document]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    # Should parse correctly despite comments
    assert "User" in parsed["Raja"]["entityTypes"]
    assert "Role" in parsed["Raja"]["entityTypes"]
    assert "Document" in parsed["Raja"]["entityTypes"]
    assert "read" in parsed["Raja"]["actions"]


@pytest.mark.unit
def test_parse_actual_raja_schema():
    """Test parsing the actual RAJA schema file."""
    # Get the path to the actual schema file
    repo_root = Path(__file__).parent.parent.parent
    schema_path = repo_root / "policies" / "schema.cedar"

    if not schema_path.exists():
        pytest.skip(f"Schema file not found at {schema_path}")

    result = load_cedar_schema_from_file(str(schema_path))
    parsed = json.loads(result)

    assert "Raja" in parsed
    assert "entityTypes" in parsed["Raja"]
    assert "actions" in parsed["Raja"]

    # Check for expected entities
    entity_types = parsed["Raja"]["entityTypes"]
    assert "User" in entity_types
    assert "Role" in entity_types
    assert "S3Bucket" in entity_types
    assert "S3Object" in entity_types
    assert "Package" in entity_types

    # Check S3Object hierarchy
    assert "S3Bucket" in entity_types["S3Object"]["memberOfTypes"]

    # Check for expected actions
    actions = parsed["Raja"]["actions"]
    assert "s3:GetObject" in actions
    assert "s3:PutObject" in actions
    assert "s3:DeleteObject" in actions
    assert "s3:ListBucket" in actions
    assert "quilt:ReadPackage" in actions

    # Verify action structure
    get_object = actions["s3:GetObject"]
    assert "appliesTo" in get_object
    assert set(get_object["appliesTo"]["principalTypes"]) == {"User", "Role"}
    assert "S3Object" in get_object["appliesTo"]["resourceTypes"]

    read_package = actions["quilt:ReadPackage"]
    assert "Package" in read_package["appliesTo"]["resourceTypes"]


@pytest.mark.unit
def test_parse_schema_with_custom_namespace():
    """Test parsing schema with custom namespace."""
    schema = """
    entity User {}
    entity Document {}

    action "read" appliesTo {
      principal: [User],
      resource: [Document]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema, namespace="CustomApp")
    parsed = json.loads(result)

    assert "CustomApp" in parsed
    assert "Raja" not in parsed


@pytest.mark.unit
def test_parse_schema_fails_without_entities():
    """Test that parsing fails if no entities are defined."""
    schema = """
    action "read" appliesTo {
      principal: [User],
      resource: [Document]
    }
    """

    with pytest.raises(ValueError, match="must contain at least one entity"):
        parse_cedar_schema_to_avp_json(schema)


@pytest.mark.unit
def test_parse_schema_fails_without_actions():
    """Test that parsing fails if no actions are defined."""
    schema = """
    entity User {}
    entity Document {}
    """

    with pytest.raises(ValueError, match="must contain at least one action"):
        parse_cedar_schema_to_avp_json(schema)


@pytest.mark.unit
def test_parse_schema_with_multiple_actions():
    """Test parsing schema with multiple S3 actions."""
    schema = """
    entity User {}
    entity Role {}
    entity S3Bucket {}
    entity S3Object in [S3Bucket] {}

    action "s3:GetObject" appliesTo {
      principal: [User, Role],
      resource: [S3Object]
    }

    action "s3:PutObject" appliesTo {
      principal: [User, Role],
      resource: [S3Object]
    }

    action "s3:ListBucket" appliesTo {
      principal: [User, Role],
      resource: [S3Bucket]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    actions = parsed["Raja"]["actions"]
    assert len(actions) == 3
    assert "s3:GetObject" in actions
    assert "s3:PutObject" in actions
    assert "s3:ListBucket" in actions

    # Verify S3Object actions point to S3Object
    assert actions["s3:GetObject"]["appliesTo"]["resourceTypes"] == ["S3Object"]
    assert actions["s3:PutObject"]["appliesTo"]["resourceTypes"] == ["S3Object"]

    # Verify S3Bucket actions point to S3Bucket
    assert actions["s3:ListBucket"]["appliesTo"]["resourceTypes"] == ["S3Bucket"]


@pytest.mark.unit
def test_parse_schema_whitespace_handling():
    """Test that parser handles various whitespace variations."""
    schema = """
    entity   User   {}
    entity Document{}

    action "read"   appliesTo   {
      principal:   [User]  ,
      resource:  [ Document ]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    # Should parse correctly despite extra whitespace
    assert "User" in parsed["Raja"]["entityTypes"]
    assert "Document" in parsed["Raja"]["entityTypes"]
    assert "read" in parsed["Raja"]["actions"]


@pytest.mark.unit
def test_parse_empty_schema():
    """Test that empty schema fails validation."""
    schema = ""

    with pytest.raises(ValueError):
        parse_cedar_schema_to_avp_json(schema)


@pytest.mark.unit
def test_parse_schema_json_is_valid():
    """Test that output is valid JSON and properly formatted."""
    schema = """
    entity User {}
    entity Document {}

    action "read" appliesTo {
      principal: [User],
      resource: [Document]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)

    # Should be valid JSON
    parsed = json.loads(result)

    # Should be properly structured
    assert isinstance(parsed, dict)
    assert "Raja" in parsed
    assert isinstance(parsed["Raja"]["entityTypes"], dict)
    assert isinstance(parsed["Raja"]["actions"], dict)

    # Should be serializable again (no weird objects)
    json.dumps(parsed)


@pytest.mark.unit
def test_load_schema_from_nonexistent_file():
    """Test that loading from nonexistent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_cedar_schema_from_file("/nonexistent/path/schema.cedar")


@pytest.mark.unit
def test_parse_schema_preserves_all_entities():
    """Test that all entities in schema are preserved in output."""
    schema = """
    entity Type1 {}
    entity Type2 {}
    entity Type3 {}
    entity Type4 {}
    entity Type5 {}

    action "action1" appliesTo {
      principal: [Type1],
      resource: [Type2, Type3, Type4, Type5]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    entities = parsed["Raja"]["entityTypes"]
    assert len(entities) == 5
    assert all(f"Type{i}" in entities for i in range(1, 6))


@pytest.mark.unit
def test_parse_schema_action_name_with_special_characters():
    """Test parsing actions with special characters like colons."""
    schema = """
    entity User {}
    entity Resource {}

    action "aws:s3:GetObject" appliesTo {
      principal: [User],
      resource: [Resource]
    }

    action "custom-action" appliesTo {
      principal: [User],
      resource: [Resource]
    }
    """

    result = parse_cedar_schema_to_avp_json(schema)
    parsed = json.loads(result)

    actions = parsed["Raja"]["actions"]
    assert "aws:s3:GetObject" in actions
    assert "custom-action" in actions
