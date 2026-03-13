from raja.rale.config import DEFAULT_PRINCIPAL
from raja.rale.select import _sorted_packages_for_principal


def test_sorted_packages_for_demo_principal_prioritizes_demo_package_grant() -> None:
    packages = ["demo/e2e", "demo/package-grant", "zeta/example"]

    assert _sorted_packages_for_principal(packages, DEFAULT_PRINCIPAL) == [
        "demo/package-grant",
        "demo/e2e",
        "zeta/example",
    ]


def test_sorted_packages_for_other_principals_stays_alphabetical() -> None:
    packages = ["zeta/example", "demo/package-grant", "demo/e2e"]

    assert _sorted_packages_for_principal(packages, "test-user") == [
        "demo/e2e",
        "demo/package-grant",
        "zeta/example",
    ]
