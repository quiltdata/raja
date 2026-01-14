#!/usr/bin/env python3
"""Version and release automation script for RAJA."""

import re
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


def get_pyproject_path() -> Path:
    """Get path to pyproject.toml."""
    return Path(__file__).parent.parent / "pyproject.toml"


def get_current_version() -> str:
    """Read version from pyproject.toml."""
    pyproject_path = get_pyproject_path()

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    version = data.get("project", {}).get("version")
    if not version:
        print("✗ Could not find version in pyproject.toml")
        sys.exit(1)

    return version


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse semantic version string into (major, minor, patch)."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        print(f"✗ Invalid version format: {version}")
        print("  Expected format: MAJOR.MINOR.PATCH (e.g., 1.2.3)")
        sys.exit(1)

    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(version: str, bump_type: str) -> str:
    """Bump version by type (major, minor, patch)."""
    major, minor, patch = parse_version(version)

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        print(f"✗ Invalid bump type: {bump_type}")
        print("  Valid types: major, minor, patch")
        sys.exit(1)


def update_pyproject_version(new_version: str) -> None:
    """Update version in pyproject.toml."""
    pyproject_path = get_pyproject_path()

    # Read the file
    with open(pyproject_path, "r") as f:
        content = f.read()

    # Replace the version line
    old_pattern = r'^version = "[^"]+"$'
    new_line = f'version = "{new_version}"'

    new_content = re.sub(old_pattern, new_line, content, count=1, flags=re.MULTILINE)

    if new_content == content:
        print("✗ Failed to update version in pyproject.toml")
        sys.exit(1)

    # Write back
    with open(pyproject_path, "w") as f:
        f.write(new_content)

    print(f"✓ Updated pyproject.toml to version {new_version}")


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


def bump_and_commit(bump_type: str = "patch") -> None:
    """Bump version and commit changes."""
    print(f"\n{'='*60}")
    print(f"Bumping {bump_type} version")
    print(f"{'='*60}\n")

    # Check git status
    check_git_status()
    check_on_branch()

    # Get current and new version
    current_version = get_current_version()
    new_version = bump_version(current_version, bump_type)

    print(f"Current version: {current_version}")
    print(f"New version: {new_version}\n")

    # Update pyproject.toml
    update_pyproject_version(new_version)

    # Update uv.lock if it exists
    lock_path = get_pyproject_path().parent / "uv.lock"
    if lock_path.exists():
        print("\n→ Updating uv.lock...")
        run_command(["uv", "lock"])
        print("✓ uv.lock updated")

    # Stage changes
    print("\n→ Staging changes...")
    run_command(["git", "add", "pyproject.toml"])
    if lock_path.exists():
        run_command(["git", "add", "uv.lock"])
    print("✓ Changes staged")

    # Commit
    commit_msg = f"Bump version to {new_version}"
    print(f"\n→ Committing: {commit_msg}")
    run_command(["git", "commit", "-m", commit_msg])
    print("✓ Changes committed")

    print(f"\n{'='*60}")
    print(f"✓ Version bumped to {new_version} and committed!")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    print(f"  1. Review the commit: git show")
    print(f"  2. Push to remote: git push")
    print(f"  3. Create release tag: ./poe tag")


def show_version() -> None:
    """Show current version."""
    version = get_current_version()
    print(version)


def create_tag() -> None:
    """Main entry point for the tag task."""
    # Parse command line arguments
    skip_checks = "--skip-checks" in sys.argv
    recreate = "--recreate" in sys.argv

    version = get_current_version()
    create_and_push_tag(version, skip_checks=skip_checks, recreate=recreate)


def main() -> None:
    """Main entry point - dispatch based on command."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/version.py show           # Show current version")
        print("  python scripts/version.py bump [type]    # Bump version (patch/minor/major)")
        print("  python scripts/version.py tag [flags]    # Create and push git tag")
        sys.exit(1)

    command = sys.argv[1]

    if command == "show":
        show_version()
    elif command == "bump":
        bump_type = sys.argv[2] if len(sys.argv) > 2 else "patch"
        if bump_type not in ["major", "minor", "patch"]:
            print(f"✗ Invalid bump type: {bump_type}")
            print("  Valid types: major, minor, patch")
            sys.exit(1)
        bump_and_commit(bump_type)
    elif command == "tag":
        create_tag()
    else:
        print(f"✗ Unknown command: {command}")
        print("  Valid commands: show, bump, tag")
        sys.exit(1)


if __name__ == "__main__":
    main()
