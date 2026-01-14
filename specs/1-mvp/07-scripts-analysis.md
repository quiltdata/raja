# RAJA Scripts Analysis

## Executive Summary

Analysis of the `/scripts/` directory reveals **mixed results**:

- ✅ **4/6 scripts are actively used** via poe tasks
- ⚠️ **1 script is redundant** (deploy.sh)
- ❌ **1 script appears unused** (test_local.py)
- ✅ **Scripts are reasonably orthogonal** with minimal overlap
- ⚠️ **Code quality varies** - some scripts need improvement

---

## Individual Script Analysis

### 1. ✅ `version.py` - **USED, WELL-WRITTEN**

**Purpose**: Version management and release automation

**Used by**:
- `./poe version` - show current version
- `./poe bump` - bump patch version
- `./poe bump-minor` - bump minor version
- `./poe bump-major` - bump major version
- `./poe tag` - create and push release tag

**Quality Assessment**: ⭐⭐⭐⭐⭐

**Strengths**:
- Comprehensive functionality (show, bump, tag)
- Good error handling and validation
- Clear user feedback with emoji markers
- Type hints throughout
- Well-documented with help text
- Follows Python best practices
- Git safety checks before operations
- Handles both local tags and remote deletion

**Weaknesses**:
- None significant

**Recommendation**: **KEEP AS-IS** - This is exemplary Python code

---

### 2. ✅ `load_policies.py` - **USED, NEEDS IMPROVEMENT**

**Purpose**: Load Cedar policy files from `policies/policies/` to AWS Verified Permissions

**Used by**: `./poe load-policies`

**Quality Assessment**: ⭐⭐⭐

**Strengths**:
- Clear purpose and scope
- Uses environment variables for configuration
- Proper error handling for missing config
- Type hints on function signatures

**Weaknesses**:
1. **No dry-run mode** - always executes operations
2. **No error handling for AWS operations** - boto3 calls can fail
3. **No validation** - doesn't check if policies are valid Cedar syntax
4. **No idempotency** - doesn't check if policy already exists
5. **No progress feedback** - silent operation
6. **Hardcoded path logic** - assumes specific directory structure

**Recommended Improvements**:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _load_policy_files(policies_dir: Path) -> list[str]:
    """Load all .cedar policy files from directory."""
    policy_files = sorted(policies_dir.glob("*.cedar"))

    if not policy_files:
        print(f"⚠ No .cedar files found in {policies_dir}")
        sys.exit(1)

    return [path.read_text(encoding="utf-8") for path in policy_files]


def _create_policy(
    client: Any,
    policy_store_id: str,
    statement: str,
    dry_run: bool = False
) -> None:
    """Create a single policy in AVP."""
    if dry_run:
        print(f"  [DRY-RUN] Would create policy: {statement[:80]}...")
        return

    try:
        client.create_policy(
            policyStoreId=policy_store_id,
            definition={"static": {"statement": statement}},
        )
        print(f"✓ Created policy")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationException":
            print(f"✗ Invalid policy syntax: {e}")
        else:
            print(f"✗ Failed to create policy: {e}")
        raise


def main() -> None:
    """Load Cedar policies to AWS Verified Permissions."""
    # Parse arguments
    dry_run = "--dry-run" in sys.argv

    # Get configuration
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    if not policy_store_id:
        raise SystemExit("✗ POLICY_STORE_ID is required")

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("✗ AWS_REGION is required")

    # Load policies
    repo_root = Path(__file__).resolve().parents[1]
    policies_dir = repo_root / "policies" / "policies"

    if not policies_dir.exists():
        raise SystemExit(f"✗ Policies directory not found: {policies_dir}")

    policies = _load_policy_files(policies_dir)

    print(f"{'='*60}")
    print(f"Loading {len(policies)} policies to AVP")
    print(f"Policy Store: {policy_store_id}")
    print(f"Region: {region}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    print(f"{'='*60}\n")

    # Create client
    client = boto3.client("verifiedpermissions", region_name=region)

    # Load each policy
    success_count = 0
    for i, statement in enumerate(policies, 1):
        print(f"[{i}/{len(policies)}] Loading policy...")
        try:
            _create_policy(client, policy_store_id, statement, dry_run)
            success_count += 1
        except Exception as e:
            print(f"  Failed: {e}")
            continue

    print(f"\n{'='*60}")
    print(f"✓ Loaded {success_count}/{len(policies)} policies successfully")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
```

**Recommendation**: **IMPROVE** with better error handling, dry-run mode, and user feedback

---

### 3. ✅ `invoke_compiler.py` - **USED, NEEDS IMPROVEMENT**

**Purpose**: Trigger the policy compiler Lambda via HTTP POST

**Used by**: `./poe compile-policies`

**Quality Assessment**: ⭐⭐

**Strengths**:
- Simple and focused
- Uses environment variables for configuration
- Type hints on function signature

**Weaknesses**:
1. **No error handling** - `urlopen` can raise exceptions
2. **No timeout** - could hang indefinitely
3. **No retry logic** - fails on transient errors
4. **No response validation** - doesn't check HTTP status codes
5. **Uses deprecated urllib** instead of modern `httpx` or `requests`
6. **No progress feedback** - silent until complete
7. **Prints raw response** - doesn't parse or format output

**Recommended Improvements**:

```python
from __future__ import annotations

import os
import sys
import time
from urllib import request
from urllib.error import HTTPError, URLError


def main() -> None:
    """Trigger policy compiler Lambda function."""
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        raise SystemExit("✗ RAJA_API_URL is required")

    url = f"{api_url.rstrip('/')}/compile"

    print(f"{'='*60}")
    print(f"Triggering policy compiler")
    print(f"URL: {url}")
    print(f"{'='*60}\n")

    print("→ Sending compile request...")

    start_time = time.time()

    try:
        req = request.Request(url, method="POST")
        with request.urlopen(req, timeout=30) as response:
            if response.status != 200:
                print(f"✗ Unexpected status code: {response.status}")
                sys.exit(1)

            body = response.read().decode("utf-8")

        elapsed = time.time() - start_time

        print(f"✓ Compilation completed in {elapsed:.2f}s\n")
        print("Response:")
        print(body)

    except HTTPError as e:
        print(f"✗ HTTP error: {e.code} {e.reason}")
        print(f"  Response: {e.read().decode('utf-8')}")
        sys.exit(1)
    except URLError as e:
        print(f"✗ Network error: {e.reason}")
        sys.exit(1)
    except TimeoutError:
        print(f"✗ Request timed out after 30s")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Better alternative**: Use `httpx` for modern async HTTP:

```python
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx


async def main() -> None:
    """Trigger policy compiler Lambda function."""
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        raise SystemExit("✗ RAJA_API_URL is required")

    url = f"{api_url.rstrip('/')}/compile"

    print(f"{'='*60}")
    print(f"Triggering policy compiler")
    print(f"URL: {url}")
    print(f"{'='*60}\n")

    print("→ Sending compile request...")

    start_time = time.time()

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url)
            response.raise_for_status()

            elapsed = time.time() - start_time

            print(f"✓ Compilation completed in {elapsed:.2f}s\n")
            print("Response:")
            print(response.text)

        except httpx.HTTPStatusError as e:
            print(f"✗ HTTP error: {e.response.status_code}")
            print(f"  Response: {e.response.text}")
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"✗ Request timed out after 30s")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Unexpected error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
```

**Recommendation**: **IMPROVE** with better error handling and modern HTTP client

---

### 4. ✅ `seed_test_data.py` - **USED, NEEDS IMPROVEMENT**

**Purpose**: Seed test principals into DynamoDB for integration testing

**Used by**: `./poe seed-test-data`

**Quality Assessment**: ⭐⭐⭐

**Strengths**:
- Clear purpose and scope
- Type hints on helper function
- Proper error handling for missing config
- Uses boto3 resource interface (cleaner than client)

**Weaknesses**:
1. **Hardcoded test data** - not configurable
2. **No dry-run mode**
3. **No error handling for DynamoDB operations**
4. **No progress feedback**
5. **Overwrites existing data** without warning
6. **Limited test coverage** - only 2 principals

**Recommended Improvements**:

```python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _get_region() -> str:
    """Get AWS region from environment."""
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("✗ AWS_REGION is required")
    return region


def _load_test_data(data_file: Path | None = None) -> dict[str, list[str]]:
    """Load test data from file or use defaults."""
    if data_file and data_file.exists():
        with open(data_file) as f:
            return json.load(f)

    # Default test data
    return {
        "alice": ["Document:doc123:read", "Document:doc123:write"],
        "bob": ["Document:doc123:read"],
        "admin": ["Document:*:*"],
        "guest": ["Document:public:read"],
    }


def _seed_principal(
    table: Any,
    principal: str,
    scopes: list[str],
    dry_run: bool = False
) -> None:
    """Seed a single principal into DynamoDB."""
    if dry_run:
        print(f"  [DRY-RUN] Would seed: {principal} with {len(scopes)} scopes")
        return

    try:
        table.put_item(Item={"principal": principal, "scopes": scopes})
        print(f"✓ Seeded principal: {principal} ({len(scopes)} scopes)")
    except ClientError as e:
        print(f"✗ Failed to seed {principal}: {e}")
        raise


def main() -> None:
    """Seed test principals into DynamoDB."""
    # Parse arguments
    dry_run = "--dry-run" in sys.argv

    # Get configuration
    table_name = os.environ.get("PRINCIPAL_TABLE")
    if not table_name:
        raise SystemExit("✗ PRINCIPAL_TABLE is required")

    # Load test data
    data_file = None
    if "--data" in sys.argv:
        idx = sys.argv.index("--data")
        if idx + 1 < len(sys.argv):
            data_file = Path(sys.argv[idx + 1])

    principals = _load_test_data(data_file)

    print(f"{'='*60}")
    print(f"Seeding {len(principals)} test principals")
    print(f"Table: {table_name}")
    print(f"Region: {_get_region()}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    print(f"{'='*60}\n")

    # Create DynamoDB resource
    dynamodb = boto3.resource("dynamodb", region_name=_get_region())
    table = dynamodb.Table(table_name)

    # Seed each principal
    success_count = 0
    for principal, scopes in principals.items():
        try:
            _seed_principal(table, principal, scopes, dry_run)
            success_count += 1
        except Exception:
            continue

    print(f"\n{'='*60}")
    print(f"✓ Seeded {success_count}/{len(principals)} principals successfully")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
```

**Recommendation**: **IMPROVE** with configurable test data, dry-run mode, and better feedback

---

### 5. ⚠️ `deploy.sh` - **REDUNDANT, DEPRECATED**

**Purpose**: Deploy CDK infrastructure

**Used by**: Not directly used - superseded by poe tasks

**Quality Assessment**: ⭐⭐

**Issues**:
1. **Redundant** - `./poe cdk-deploy` does the same thing with better integration
2. **Less flexible** - no options for diff, synth, destroy
3. **Manual dependency installation** - `uv pip install aws-cdk-lib` is better in pyproject.toml
4. **No error handling** - fails silently on errors
5. **Uses pushd/popd** - unnecessary complexity
6. **No progress feedback**

**Current poe tasks that replace it**:
```toml
cdk-synth = { sequence = ["npx-verify", "cdk-synth-cmd"] }
cdk-diff = { sequence = ["npx-verify", "cdk-diff-cmd"] }
cdk-deploy = { sequence = ["npx-verify", "cdk-deploy-cmd"] }
cdk-destroy = { sequence = ["npx-verify", "cdk-destroy-cmd"] }
```

**Comparison**:

| Feature | deploy.sh | poe tasks |
|---------|-----------|-----------|
| Synth | ✅ | ✅ |
| Deploy | ✅ | ✅ |
| Diff | ❌ | ✅ |
| Destroy | ❌ | ✅ |
| Error handling | ❌ | ✅ |
| Help text | ❌ | ✅ |
| CI/CD ready | ❌ | ✅ |

**Recommendation**: **DELETE** - Completely replaced by poe tasks

---

### 6. ❌ `test_local.py` - **APPEARS UNUSED**

**Purpose**: Test the raja library locally without AWS

**Used by**: No poe task, no CI workflow, no documentation

**Quality Assessment**: ⭐⭐⭐

**Analysis**:
- **Not referenced in pyproject.toml** - no poe task
- **Not mentioned in CLAUDE.md** - no documentation
- **Not used in CI/CD** - no workflow references
- **Overlaps with unit tests** - tests/unit/ covers same ground

**Script functionality**:
```python
def main() -> None:
    # Compile policies
    policies = ['permit(principal == User::"alice", ...)']
    compiled = compile_policies(policies)
    scopes = compiled.get("alice", [])

    # Create token
    token = create_token("alice", scopes, ttl=3600, secret="local-dev-secret")

    # Enforce authorization
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token, request, secret)

    print("Token scopes:", scopes)
    print("Decision:", decision.model_dump())
```

**This is better served by**:
1. **Unit tests** - `tests/unit/` already covers this functionality
2. **Interactive REPL** - `./poe repl` for manual testing
3. **Integration tests** - `tests/integration/` for end-to-end validation

**Recommendation**: **DELETE** - Redundant with existing test infrastructure

---

## Orthogonality Analysis

### Script Dependencies and Overlap

```
version.py         → [Independent] Version management
load_policies.py   → [Depends on CDK] Loads policies to AVP
invoke_compiler.py → [Depends on CDK] Triggers compiler
seed_test_data.py  → [Depends on CDK] Seeds test data
deploy.sh          → [REDUNDANT] Replaced by poe tasks
test_local.py      → [REDUNDANT] Covered by unit tests
```

### Orthogonality Score: ⭐⭐⭐⭐ (4/5)

**Good separation**:
- ✅ version.py is completely independent
- ✅ load_policies.py handles policy loading
- ✅ invoke_compiler.py handles compilation
- ✅ seed_test_data.py handles test data

**Issues**:
- ⚠️ deploy.sh duplicates poe task functionality
- ⚠️ test_local.py duplicates unit test functionality

**After removing deploy.sh and test_local.py**: ⭐⭐⭐⭐⭐ (5/5) - Perfect orthogonality

---

## Summary Matrix

| Script | Used? | Quality | Orthogonal? | Recommendation |
|--------|-------|---------|-------------|----------------|
| version.py | ✅ | ⭐⭐⭐⭐⭐ | ✅ | Keep as-is |
| load_policies.py | ✅ | ⭐⭐⭐ | ✅ | Improve |
| invoke_compiler.py | ✅ | ⭐⭐ | ✅ | Improve |
| seed_test_data.py | ✅ | ⭐⭐⭐ | ✅ | Improve |
| deploy.sh | ❌ | ⭐⭐ | ❌ | Delete |
| test_local.py | ❌ | ⭐⭐⭐ | ❌ | Delete |

---

## Recommendations

### Immediate Actions

1. **DELETE** `deploy.sh` - Completely replaced by poe tasks
   ```bash
   git rm scripts/deploy.sh
   ```

2. **DELETE** `test_local.py` - Redundant with unit tests
   ```bash
   git rm scripts/test_local.py
   ```

### Short-term Improvements (High Priority)

3. **IMPROVE** `invoke_compiler.py`
   - Add timeout and error handling
   - Add progress feedback
   - Consider switching to httpx for better HTTP handling

4. **IMPROVE** `load_policies.py`
   - Add dry-run mode
   - Add error handling for AWS operations
   - Add progress feedback
   - Add policy validation

### Medium-term Improvements

5. **IMPROVE** `seed_test_data.py`
   - Make test data configurable (JSON file)
   - Add dry-run mode
   - Add more comprehensive test principals
   - Add error handling

### Optional Enhancements

6. **Consider**: Move scripts to `src/raja/cli/` for better packaging
   - Makes scripts installable as entry points
   - Better for distribution via PyPI
   - Cleaner project structure

7. **Consider**: Add a unified CLI interface
   ```bash
   raja policy load      # instead of python scripts/load_policies.py
   raja policy compile   # instead of python scripts/invoke_compiler.py
   raja test seed        # instead of python scripts/seed_test_data.py
   raja version show     # instead of python scripts/version.py show
   ```

---

## Code Quality Standards

### All scripts should have:

1. ✅ **Type hints** on all functions
2. ✅ **Error handling** for all external operations
3. ✅ **Progress feedback** for long-running operations
4. ✅ **Dry-run mode** for destructive operations
5. ✅ **Clear exit codes** (0 for success, 1+ for errors)
6. ✅ **User-friendly output** with emoji status markers
7. ✅ **Environment variable validation**
8. ✅ **Docstrings** on all functions
9. ✅ **Command-line argument parsing** where appropriate
10. ✅ **Timeout handling** for network operations

### Example template:

```python
#!/usr/bin/env python3
"""Brief description of script purpose."""

from __future__ import annotations

import os
import sys
from typing import Any


def main() -> None:
    """Main entry point with clear docstring."""
    # Validate environment
    required_var = os.environ.get("REQUIRED_VAR")
    if not required_var:
        print("✗ REQUIRED_VAR is required", file=sys.stderr)
        sys.exit(1)

    # Parse arguments
    dry_run = "--dry-run" in sys.argv

    # Show what we're doing
    print(f"{'='*60}")
    print(f"Script purpose description")
    if dry_run:
        print("Mode: DRY-RUN")
    print(f"{'='*60}\n")

    # Do work with error handling
    try:
        print("→ Starting operation...")
        # ... do work ...
        print("✓ Operation completed successfully")
    except Exception as e:
        print(f"✗ Operation failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Integration with Poe Tasks

### Current integration:

```toml
[tool.poe.tasks]
load-policies = { cmd = "python scripts/load_policies.py", help = "Load Cedar policies to AVP" }
compile-policies = { cmd = "python scripts/invoke_compiler.py", help = "Compile policies to scopes" }
seed-test-data = { cmd = "python scripts/seed_test_data.py", help = "Seed integration test principals into DynamoDB" }
version = { script = "scripts.version:show_version", help = "Show current version" }
bump = { script = "scripts.version:bump_and_commit", help = "Bump patch version and commit (default)" }
bump-minor = { cmd = "python scripts/version.py bump minor", help = "Bump minor version and commit" }
bump-major = { cmd = "python scripts/version.py bump major", help = "Bump major version and commit" }
tag = { script = "scripts.version:create_tag", help = "Create and push a git tag for release (runs checks first)" }
```

### After cleanup:

```toml
[tool.poe.tasks]
# Policy management (AWS dependent)
load-policies = { cmd = "python scripts/load_policies.py", help = "Load Cedar policies to AVP" }
compile-policies = { cmd = "python scripts/invoke_compiler.py", help = "Compile policies to scopes" }
seed-test-data = { cmd = "python scripts/seed_test_data.py", help = "Seed integration test principals into DynamoDB" }

# Version management (pure Python, no AWS)
version = { script = "scripts.version:show_version", help = "Show current version" }
bump = { script = "scripts.version:bump_and_commit", help = "Bump patch version and commit (default)" }
bump-minor = { cmd = "python scripts/version.py bump minor", help = "Bump minor version and commit" }
bump-major = { cmd = "python scripts/version.py bump major", help = "Bump major version and commit" }
tag = { script = "scripts.version:create_tag", help = "Create and push a git tag for release" }
```

---

## Conclusion

The scripts directory is **mostly well-organized** but needs **cleanup and improvements**:

### Strengths:
- ✅ Good separation of concerns (after cleanup)
- ✅ Most scripts have clear, single purposes
- ✅ Integrated with poe task runner
- ✅ version.py is exemplary code

### Weaknesses:
- ❌ Two unused/redundant scripts (deploy.sh, test_local.py)
- ⚠️ Inconsistent error handling
- ⚠️ Limited user feedback during operations
- ⚠️ No dry-run modes for AWS operations

### Priority actions:
1. **Delete** deploy.sh and test_local.py (redundant)
2. **Improve** error handling in load_policies.py and invoke_compiler.py
3. **Add** dry-run modes to all AWS scripts
4. **Enhance** user feedback with progress indicators

After these improvements, the scripts directory will be:
- ✅ 100% used (4/4 scripts)
- ✅ 100% orthogonal (no overlap)
- ✅ Well-written (consistent quality)
