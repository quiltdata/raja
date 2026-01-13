from __future__ import annotations

import re

from ..models import CedarPolicy

_EFFECT_RE = re.compile(r"^(permit|forbid)\s*\(", re.IGNORECASE)
_FIELD_RE = re.compile(r"\b(principal|action|resource)\s*==\s*([^,\)]+)", re.IGNORECASE)


def parse_policy(policy_str: str) -> CedarPolicy:
    cleaned = policy_str.strip().rstrip(";")
    effect_match = _EFFECT_RE.match(cleaned)
    if not effect_match:
        raise ValueError("policy must start with permit(...) or forbid(...)")

    effect = effect_match.group(1).lower()
    fields = {key.lower(): value.strip() for key, value in _FIELD_RE.findall(cleaned)}

    if not {"principal", "action", "resource"}.issubset(fields.keys()):
        raise ValueError("policy must include principal, action, and resource")

    return CedarPolicy(
        effect=effect,
        principal=fields["principal"],
        action=fields["action"],
        resource=fields["resource"],
    )
