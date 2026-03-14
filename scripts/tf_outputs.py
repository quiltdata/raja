from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUTS_PATH = _REPO_ROOT / "infra" / "tf-outputs.json"


def load_tf_outputs() -> dict[str, Any]:
    if not _OUTPUTS_PATH.is_file():
        return {}
    try:
        payload = json.loads(_OUTPUTS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_tf_output(key: str) -> str | None:
    value = load_tf_outputs().get(key)
    return value if isinstance(value, str) and value else None
