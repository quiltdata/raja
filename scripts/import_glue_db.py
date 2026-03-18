"""Grant Lake Formation access to the Iceberg Glue database for all project roles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from seed_config import load_seed_config
from tf_outputs import get_tf_output

BYOGDC = (
    Path(__file__).resolve().parents[2]
    / "Unified-Studio-for-Amazon-Sagemaker"
    / "migration"
    / "bring-your-own-gdc-assets"
    / "bring_your_own_gdc_assets.py"
)

DATABASE = "icebergdatabase-v9cxuqnwjj5a"
REGION = "us-east-1"

SEED_CONFIG = load_seed_config()
ROLES = [
    (project.display_name, f"datazone_{project.project_name}_environment_role_arn")
    for project in SEED_CONFIG.projects
]


def main() -> None:
    if not BYOGDC.is_file():
        sys.exit(f"byogdc script not found: {BYOGDC}")

    for label, key in ROLES:
        role_arn = get_tf_output(key)
        if not role_arn:
            sys.exit(f"Missing tf-output: {key}")

        print(f"\n=== {label}: {role_arn} ===")
        subprocess.run(
            [
                sys.executable,
                str(BYOGDC),
                "--project-role-arn", role_arn,
                "--database-name", DATABASE,
                "--region", REGION,
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
