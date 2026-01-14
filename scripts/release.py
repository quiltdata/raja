#!/usr/bin/env python3
"""Release automation script for RAJA."""

import subprocess
import sys
import tomllib
from pathlib import Path


def run_command(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    print(f"→ Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0 and check:
        print(f"✗ Command failed with exit code {result.returncode}")
        if result.stderr:
            print(f"  Error: {result.stderr}")
        sys.exit(result.returncode)

    return result


def get_current_version() -> str:
    """Read version from pyproject.toml."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    version = data.get("project", {}).get("version")
    if not version:
        print("✗ Could not find version in pyproject.toml")
        sys.exit(1)

    return version


def check_git_status() -> None:
    """Verify git working directory is clean."""
    result = run_command(["git", "status", "--porcelain"])

    if result.stdout.strip():
        print("✗ Git working directory is not clean. Please commit or stash changes first.")
        print("\nUncommitted changes:")
        print(result.stdout)
        sys.exit(1)

    print("✓ Git working directory is clean")


def check_on_branch() -> str:
    """Get current branch name."""
    result = run_command(["git", "branch", "--show-current"])
    branch = result.stdout.strip()

    if not branch:
        print("✗ Not on a branch (detached HEAD)")
        sys.exit(1)

    print(f"✓ On branch: {branch}")
    return branch


def run_quality_checks() -> None:
    """Run quality checks before tagging."""
    print("\n→ Running quality checks...")
    run_command(["./poe", "check"])
    print("✓ Quality checks passed")


def run_tests() -> None:
    """Run tests before tagging."""
    print("\n→ Running tests...")
    run_command(["./poe", "test-unit"])
    print("✓ Tests passed")


def tag_exists(tag: str) -> bool:
    """Check if a git tag already exists."""
    result = run_command(["git", "tag", "-l", tag], check=False)
    return bool(result.stdout.strip())


def create_and_push_tag(version: str, skip_checks: bool = False, recreate: bool = False) -> None:
    """Create and push a git tag for the given version."""
    tag = f"v{version}"

    print(f"\n{'='*60}")
    print(f"Creating release tag: {tag}")
    print(f"{'='*60}\n")

    # Check git status
    check_git_status()
    branch = check_on_branch()

    # Handle existing tag
    if tag_exists(tag):
        if recreate:
            print(f"⚠ Tag {tag} already exists - deleting for recreation")
            print(f"\n→ Deleting local tag {tag}...")
            run_command(["git", "tag", "-d", tag])
            print(f"✓ Local tag deleted")

            print(f"\n→ Deleting remote tag {tag}...")
            run_command(["git", "push", "origin", f":refs/tags/{tag}"])
            print(f"✓ Remote tag deleted")
        else:
            print(f"✗ Tag {tag} already exists")
            print("\nExisting tags:")
            run_command(["git", "tag", "-l", "v*"])
            print(f"\nUse --recreate to delete and recreate the tag")
            sys.exit(1)

    # Run quality checks unless skipped
    if not skip_checks:
        run_quality_checks()
        run_tests()
    else:
        print("\n⚠ Skipping quality checks and tests")

    # Create tag
    print(f"\n→ Creating tag {tag}...")
    run_command(["git", "tag", "-a", tag, "-m", f"Release {version}"])
    print(f"✓ Tag {tag} created")

    # Push tag
    print(f"\n→ Pushing tag {tag} to origin...")
    result = run_command(["git", "push", "origin", tag])
    print(f"✓ Tag {tag} pushed to origin")

    print(f"\n{'='*60}")
    print(f"✓ Release tag {tag} created and pushed successfully!")
    print(f"{'='*60}")
    print("\nWhat happens next:")
    print("  1. GitHub Actions will trigger the release workflow")
    print("  2. Quality checks and tests will run")
    print("  3. Package will be built and published to PyPI")
    print("  4. Release assets will be uploaded to GitHub")
    print(f"\nView the release workflow at:")
    print(f"  https://github.com/YOUR_ORG/raja/actions")


def create_tag() -> None:
    """Main entry point for the tag task."""
    # Parse command line arguments
    skip_checks = "--skip-checks" in sys.argv
    recreate = "--recreate" in sys.argv

    version = get_current_version()
    create_and_push_tag(version, skip_checks=skip_checks, recreate=recreate)


if __name__ == "__main__":
    create_tag()
