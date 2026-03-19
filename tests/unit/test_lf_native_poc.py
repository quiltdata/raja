from __future__ import annotations

from scripts.lf_native_poc.create_throwaway_subscriber import _timestamped_name
from scripts.lf_native_poc.package_tag_import_poc import (
    _extract_provisioned_value,
    _score_asset_as_imported_candidate,
)


def test_extract_provisioned_value_returns_matching_value() -> None:
    environment = {
        "provisionedResources": [
            {"name": "glueDBName", "value": "db1"},
            {"name": "userRoleArn", "value": "arn:aws:iam::123:role/example"},
        ]
    }

    assert _extract_provisioned_value(environment, "userRoleArn") == "arn:aws:iam::123:role/example"


def test_score_asset_as_imported_candidate_prefers_new_asset_and_reference_form() -> None:
    asset = {
        "id": "new-asset",
        "createdBy": "SYSTEM",
        "formsOutput": [
            {"formName": "GlueTableForm"},
            {"formName": "DataSourceReferenceForm"},
        ],
    }

    score, reasons = _score_asset_as_imported_candidate(asset, known_asset_ids={"old-asset"})

    assert score > 0
    assert "new asset id after import run" in reasons
    assert "has DataSourceReferenceForm" in reasons
    assert "created by SYSTEM" in reasons


def test_timestamped_name_prefixes_value() -> None:
    assert _timestamped_name("raja-throwaway").startswith("raja-throwaway-")
