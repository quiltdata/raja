from __future__ import annotations

from unittest.mock import patch

import pytest

from raja.rale.access import _collect_access_audits, run_access_audit
from raja.rale.console import Console
from raja.rale.state import ResolvedConfig, SessionState


def _state() -> SessionState:
    return SessionState(
        config=ResolvedConfig(
            server_url="https://api.example.com",
            registry="s3://registry-bucket",
            rajee_endpoint="https://rajee.example.com",
            admin_key="admin-key",
            principal="arn:aws:iam::123456789012:user/alice",
            tf_dir="infra/terraform",
            rale_authorizer_url="https://authorizer.example.com",
            rale_router_url="https://router.example.com",
        )
    )


def test_collect_access_audits_classifies_inaccessible_package() -> None:
    payload = {
        "principal_summary": [
            {
                "principal": "arn:aws:iam::123456789012:user/alice",
                "project_ids": ["proj-alpha"],
                "project_names": ["Alpha"],
            }
        ],
        "packages": [
            {
                "package_name": "alpha/home",
                "owner_project_id": "proj-alpha",
                "owner_project_name": "Alpha",
            },
            {
                "package_name": "bio/home",
                "owner_project_id": "proj-bio",
                "owner_project_name": "Bio",
            },
            {
                "package_name": "compute/home",
                "owner_project_id": "proj-compute",
                "owner_project_name": "Compute",
            },
        ],
        "access": [
            {
                "principal_project_id": "proj-alpha",
                "package_name": "alpha/home",
                "access_mode": "OWNED",
            },
            {
                "principal_project_id": "proj-alpha",
                "package_name": "compute/home",
                "access_mode": "GRANTED",
            },
        ],
    }

    with patch("raja.rale.access._admin_get_json", return_value=payload):
        with patch(
            "raja.rale.access._build_sample_usl",
            side_effect=lambda **kwargs: ("data.csv", f"usl://{kwargs['package_name']}"),
        ):
            project_names, audits = _collect_access_audits(_state())

    assert project_names == ["Alpha"]
    assert [(item.package_name, item.access_mode) for item in audits] == [
        ("alpha/home", "OWNED"),
        ("bio/home", "INACCESSIBLE"),
        ("compute/home", "GRANTED"),
    ]


def test_run_access_audit_requires_denial_for_inaccessible_package() -> None:
    audits = [
        ("alpha/home", "OWNED"),
        ("bio/home", "GRANTED"),
        ("compute/home", "INACCESSIBLE"),
    ]

    with patch(
        "raja.rale.access._collect_access_audits",
        return_value=(
            ["Alpha"],
            [
                type(
                    "Audit",
                    (),
                    {
                        "package_name": package_name,
                        "owner_project_id": f"proj-{package_name}",
                        "owner_project_name": package_name.split("/")[0].title(),
                        "access_mode": access_mode,
                        "sample_path": "data.csv",
                        "usl": f"usl://{package_name}",
                    },
                )()
                for package_name, access_mode in audits
            ],
        ),
    ):
        with patch("raja.rale.access.run_fetch") as fetch_mock:
            with patch("raja.rale.access.run_authorize") as authorize_mock:

                def _authorize_side_effect(*, state: SessionState, console: Console) -> None:
                    if state.usl == "usl://compute/home":
                        raise RuntimeError(
                            "DENY - no DataZone package grant permits this principal + package"
                        )

                authorize_mock.side_effect = _authorize_side_effect
                run_access_audit(_state(), Console())

    assert authorize_mock.call_count == 3
    assert fetch_mock.call_count == 2


def test_run_access_audit_fails_if_inaccessible_package_is_allowed() -> None:
    with patch(
        "raja.rale.access._collect_access_audits",
        return_value=(
            ["Alpha"],
            [
                type(
                    "Audit",
                    (),
                    {
                        "package_name": "compute/home",
                        "owner_project_id": "proj-compute",
                        "owner_project_name": "Compute",
                        "access_mode": "INACCESSIBLE",
                        "sample_path": "data.csv",
                        "usl": "usl://compute/home",
                    },
                )()
            ],
        ),
    ):
        with patch("raja.rale.access.run_authorize", return_value=None):
            with pytest.raises(
                RuntimeError,
                match="Expected inaccessible package compute/home to be denied",
            ):
                run_access_audit(_state(), Console())
