import pytest

from scripts import version


def test_parse_bump_type_defaults_to_patch() -> None:
    assert version.parse_bump_type([]) == "patch"


@pytest.mark.parametrize("bump_type", ["patch", "minor", "major"])
def test_parse_bump_type_accepts_single_valid_value(bump_type: str) -> None:
    assert version.parse_bump_type([bump_type]) == bump_type


def test_parse_bump_type_rejects_ambiguous_args() -> None:
    with pytest.raises(SystemExit):
        version.parse_bump_type(["patch", "minor"])
