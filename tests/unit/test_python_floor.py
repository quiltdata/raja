"""Guard the minimum Python version declared in pyproject.toml.

The published package and the Lambda bundles must parse on the oldest
interpreter ``requires-python`` allows. The CI matrix cannot be relied on for
this: ``uv sync`` resolves the interpreter from ``.python-version``, so every
matrix job runs the same version regardless of ``actions/setup-python``.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "lambda_handlers")


def minimum_python() -> tuple[int, int]:
    """Return the lowest (major, minor) accepted by ``requires-python``."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    requires_python = pyproject["project"]["requires-python"]
    match = re.search(r">=\s*(\d+)\.(\d+)", requires_python)
    if not match:
        pytest.fail(f"cannot read a minimum version from requires-python: {requires_python}")
    return int(match.group(1)), int(match.group(2))


def source_files() -> list[Path]:
    return sorted(path for root in SOURCE_ROOTS for path in root.rglob("*.py"))


def test_sources_parse_on_minimum_python() -> None:
    floor = minimum_python()
    failures: list[str] = []

    for path in source_files():
        try:
            ast.parse(path.read_text(), filename=str(path), feature_version=floor)
        except SyntaxError as exc:
            relative = path.relative_to(REPO_ROOT)
            failures.append(f"{relative}:{exc.lineno}: {exc.msg}")

    assert not failures, "syntax not supported on Python {}.{}:\n{}".format(
        floor[0], floor[1], "\n".join(failures)
    )


def test_feature_version_rejects_newer_syntax() -> None:
    """Fail loudly if ``feature_version`` stops gating syntax we rely on it for."""
    floor = minimum_python()
    if floor >= (3, 14):
        pytest.skip("minimum Python already allows unparenthesized except expressions")

    source = "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n"
    with pytest.raises(SyntaxError):
        ast.parse(source, feature_version=floor)
