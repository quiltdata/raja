from raja.rale.config import DEFAULT_PRINCIPAL
from raja.rale.select import _sorted_packages_for_principal


def test_sorted_packages_for_demo_principal_prioritizes_seeded_packages() -> None:
    packages = ["demo/e2e", "compute/home", "zeta/example", "alpha/home"]

    assert _sorted_packages_for_principal(packages, DEFAULT_PRINCIPAL) == [
        "alpha/home",
        "compute/home",
        "demo/e2e",
        "zeta/example",  # alphabetical within the non-seeded remainder
    ]


def test_sorted_packages_for_other_principals_stays_alphabetical() -> None:
    packages = ["zeta/example", "compute/home", "bio/home"]

    assert _sorted_packages_for_principal(packages, "alice") == [
        "bio/home",
        "compute/home",
        "zeta/example",
    ]
