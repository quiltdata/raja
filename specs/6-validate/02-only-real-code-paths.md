# Only Real Code Paths: Structural Guarantees Against Faking

## What the Existing Tests Actually Test (spoiler: not much)

After removing the DynamoDB tables (spec 01), the handlers still have no proven
integration with real Quilt packages. Here is what each test actually exercises today:

### test_rale_complete_flow_end_to_end (test_rale_end_to_end.py:216)

The docstring says "COMPREHENSIVE RALE SYSTEM TEST" and lists six steps.
What it actually does:

1. Calls `/health` — real
2. Calls `/.well-known/jwks.json` — real
3. Calls `POST /token` — real
4. **Seeds `manifest_cache` with fake entries (lines 292–304)** — fake
5. **Seeds `taj_cache` with a hand-crafted TAJ (lines 321–329)** — fake
6. Calls authorizer → gets back the TAJ WE PUT THERE — fake
7. Calls router → reads from the manifest WE PUT THERE — fake

The "SYSTEM TEST PASSED" banner is a lie. The system was never exercised.

### test_rale_router_direct_invocation_with_manifest_membership (line 115)

Seeds manifest_cache directly (lines 146–159). Router never calls quilt3.
Tests that the router can read from DynamoDB and proxy S3. That is all.

### test_rale_router_cache_miss_exercises_manifest_resolution_path (line 172)

The only test that attempts the cache-miss path. It uses a random fake registry
(`registry-{uuid}`), then asserts `status in (502, 403, 404)`. It passes whether
or not quilt3 is installed or working. It only proves that the code *doesn't crash*
on a missing import — not that it resolves real packages.

---

## The Escape Hatches (and how to close them)

| Escape hatch | How tests exploit it | How to close it |
|---|---|---|
| `taj_cache` DynamoDB table | Pre-seed a TAJ → authorizer returns it, never calls AVP | Remove table (spec 01) |
| `manifest_cache` DynamoDB table | Pre-seed entries → router uses them, never calls quilt3 | Remove table (spec 01) |
| `manifest_cache` "latest pointer" | Pre-seed `pkg:{registry}/{pkg}` → authorizer resolves hash from DB, not quilt3 | Remove table AND rewrite authorizer to call quilt3 |
| `pytest.skip()` in `require_*` helpers | Missing env var → test silently skipped, CI shows green | Replace with `pytest.fail()` for RALE test URI |
| Cache-miss test accepts 502/403/404 | Fake package always fails → test always passes | Remove: this test proves nothing |

---

## Changes

### 1. `infra/terraform/main.tf`

Delete the `manifest_cache` and `taj_cache` DynamoDB table resources.
Delete any IAM statements that grant Lambda access to those tables.
Delete env var bindings (`MANIFEST_CACHE_TABLE`, `TAJ_CACHE_TABLE`) from Lambda
function configurations.

### 2. `infra/terraform/outputs.tf`

Delete `manifest_cache_table_name` and `taj_cache_table_name` outputs.

### 3. `lambda_handlers/rale_authorizer/handler.py`

Remove `_resolve_manifest_hash()`. Remove all DynamoDB usage.

For **un-pinned USLs**, resolve the latest hash via quilt3:

```python
def _resolve_latest_hash_via_quilt3(registry: str, package_name: str) -> str:
    try:
        import quilt3  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("quilt3 is required for package resolution") from exc
    pkg = quilt3.Package.browse(
        name=package_name,
        registry=f"s3://{registry}",
        # no top_hash → fetches latest
    )
    return str(pkg.top_hash)
```

Remove `TAJ_CACHE_TABLE` and `MANIFEST_CACHE_TABLE` from required env vars.
Remove the cache read block (lines 156–171) and the cache write block (lines 244–255).

The handler flow simplifies to:
1. Parse principal and USL from request
2. For un-pinned USL: call `_resolve_latest_hash_via_quilt3()`
3. Call AVP `is_authorized()`
4. If DENY → 403
5. Load JWT secret from Secrets Manager
6. Create and return TAJ — no caching, no DynamoDB

### 4. `lambda_handlers/rale_router/handler.py`

Remove `_load_cached_manifest()`, `_store_manifest()`. Remove all DynamoDB usage.

Replace the cache-read + conditional-resolve block (lines 209–232) with:

```python
storage = os.environ.get("RALE_STORAGE", "s3")
quilt_uri = _build_quilt_uri(storage, registry, package_name, manifest_hash)
try:
    package_map = resolve_package_map(quilt_uri)
except RuntimeError as exc:
    return _response(502, {"error": f"manifest resolution unavailable: {exc}"})
except Exception:
    return _response(502, {"error": "manifest resolution failed"})

entries = {
    logical: [loc.model_dump(mode="json") for loc in locations]
    for logical, locations in package_map.entries.items()
}
```

Remove `MANIFEST_CACHE_TABLE` from required env vars.

### 5. `scripts/seed_packages.py`

Two changes:

**a) Expose `SEED_FILES` as a module-level constant** (not inside a function) so
tests can import the exact bytes:

```python
SEED_FILES: dict[str, bytes] = {
    "data.csv":     b"col1,col2,col3\nalpha,1,true\nbeta,2,false\ngamma,3,true\n",
    "README.md":    b"# Package Demo\n\nSample dataset for RAJA package-grant integration tests.\n\n"
                    b"## Files\n\n- `data.csv` — tabular data\n- `results.json` — summary stats\n",
    "results.json": b'{"status": "ok", "row_count": 3, "columns": ["col1", "col2", "col3"]}\n',
}
```

**b) After a successful push, write `.rale-test-uri`** to the repo root:

```python
from pathlib import Path

# After successful push:
uri = f"quilt+s3://{registry_bucket}#package=demo/package-grant@{top_hash}"
uri_file = Path(__file__).resolve().parents[1] / ".rale-test-uri"
uri_file.write_text(uri + "\n")

print()
print(f"export RALE_TEST_QUILT_URI={uri}")
print(f"(also written to {uri_file})")
```

Add `.rale-test-uri` to `.gitignore`.

### 6. `tests/integration/helpers.py`

**Remove** `require_taj_cache_table()`, `require_manifest_cache_table()`,
`_load_taj_cache_table_from_outputs()`, `_load_manifest_cache_table_from_outputs()`.

**Add** `require_rale_test_quilt_uri()`:

```python
_RALE_URI_FILE = _REPO_ROOT / ".rale-test-uri"

def require_rale_test_quilt_uri() -> str:
    """Return RALE_TEST_QUILT_URI or FAIL LOUDLY — never skip."""
    uri = os.environ.get("RALE_TEST_QUILT_URI")
    if not uri and _RALE_URI_FILE.is_file():
        uri = _RALE_URI_FILE.read_text().strip()
    if not uri:
        pytest.fail(
            "RALE_TEST_QUILT_URI is not set and .rale-test-uri does not exist.\n"
            "Run: python scripts/seed_packages.py\n"
            "Then set RALE_TEST_QUILT_URI=<printed URI> or rely on .rale-test-uri"
        )
    return uri  # type: ignore[return-value]  # pytest.fail() never returns
```

**`pytest.fail()` not `pytest.skip()`** — a missing URI means the test suite is
broken, not that it should be silently omitted from CI results.

**Add** `parse_rale_test_quilt_uri()`:

```python
def parse_rale_test_quilt_uri(uri: str) -> dict[str, str]:
    """Parse quilt+s3://bucket#package=name@hash → dict with keys:
       storage, registry, package_name, hash
    """
    # uri format: quilt+s3://{bucket}#package={author}/{name}@{hash}
    storage = uri.split("://")[0].removeprefix("quilt+")       # "s3"
    rest = uri.split("://", 1)[1]                               # "bucket#package=..."
    registry, fragment = rest.split("#", 1)                     # "bucket", "package=..."
    pkg_ref = fragment.removeprefix("package=")                 # "author/name@hash"
    package_name, top_hash = pkg_ref.rsplit("@", 1)
    return {
        "storage": storage,
        "registry": registry,
        "package_name": package_name,
        "hash": top_hash,
    }
```

### 7. `tests/integration/test_rale_end_to_end.py`

Delete all four existing tests. Write three replacements. **No DynamoDB. No fake data.**

```python
from scripts.seed_packages import SEED_FILES
from .helpers import (
    fetch_jwks_secret,
    parse_rale_test_quilt_uri,
    request_url,
    require_rale_authorizer_url,
    require_rale_router_url,
    require_rale_test_quilt_uri,
    require_rajee_endpoint,
)


@pytest.mark.integration
def test_rale_authorizer_mints_taj_for_real_package() -> None:
    """Authorizer resolves the latest hash via quilt3 and mints a real TAJ.

    Passes only if:
    - quilt3 is installed in the Lambda
    - The seeded package exists in the registry
    - AVP issues an ALLOW decision for the principal
    """
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)

    principal = "test-user"
    # Un-pinned USL — no @hash; authorizer must resolve it via quilt3
    usl_path = f"/{parts['registry']}/{parts['package_name']}/data.csv"
    encoded_usl_path = quote(usl_path, safe="/@")

    rajee_endpoint = require_rajee_endpoint()
    status, _, body = request_url(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")

    payload = json.loads(body)
    assert payload["cached"] is False, "TAJ must be freshly minted, not pre-seeded"

    resolved_hash = payload["manifest_hash"]
    assert resolved_hash == parts["hash"], (
        f"Authorizer resolved hash {resolved_hash!r} "
        f"but seed_packages.py recorded {parts['hash']!r}"
    )


@pytest.mark.integration
def test_rale_router_fetches_real_s3_object() -> None:
    """Router calls quilt3 to resolve the package map and returns exact file bytes.

    Passes only if:
    - quilt3 is installed in the Lambda
    - The seeded package exists and contains data.csv
    - The file bytes match what seed_packages.py wrote to S3
    """
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)

    principal = "test-user"
    jwt_secret = fetch_jwks_secret()

    # Mint a TAJ directly (signed with the real JWKS secret) for the pinned package
    now = int(time.time())
    manifest_hash = parts["hash"]
    package_name = parts["package_name"]
    registry = parts["registry"]
    taj = jwt.encode(
        {
            "sub": principal,
            "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
            "iat": now,
            "exp": now + 3600,
        },
        jwt_secret,
        algorithm="HS256",
    )

    # Pinned USL — exact hash in URL
    usl_path = f"/{registry}/{package_name}@{manifest_hash}/data.csv"
    encoded_usl_path = quote(usl_path, safe="/@")

    router_url = require_rale_router_url()
    status, _, body = request_url(
        "GET",
        f"{router_url}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == SEED_FILES["data.csv"], (
        f"Router returned {body!r}, expected {SEED_FILES['data.csv']!r}"
    )


@pytest.mark.integration
def test_rale_complete_flow_no_preseeding() -> None:
    """Full roundtrip with zero pre-seeding: authorizer → quilt3 → TAJ → router → S3.

    Steps:
      1. Control plane health + JWKS
      2. Authorizer: un-pinned USL → quilt3 resolves hash → TAJ minted
      3. Assert resolved_hash == seeded hash
      4. Router: un-pinned USL + TAJ → quilt3 resolves manifest → S3 object returned
      5. Assert body == SEED_FILES["README.md"] (exact bytes)

    NO manifest_cache.put_item()
    NO taj_cache.put_item()
    """
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    principal = "test-user"
    logical_key = "README.md"

    usl_path = f"/{parts['registry']}/{parts['package_name']}/{logical_key}"
    encoded_usl_path = quote(usl_path, safe="/@")

    # 1. Control plane
    from .helpers import request_json
    status, _ = request_json("GET", "/health")
    assert status == 200

    # 2. Authorizer — un-pinned USL, no @hash
    rajee_endpoint = require_rajee_endpoint()
    status, _, body = request_url(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode()
    payload = json.loads(body)
    taj = payload["token"]
    assert payload["cached"] is False
    assert payload["manifest_hash"] == parts["hash"]

    # 3. Router — un-pinned USL + TAJ from authorizer
    status, _, body = request_url(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
    )
    assert status == 200, body.decode()
    assert body == SEED_FILES["README.md"], (
        f"Router returned {body!r}, expected {SEED_FILES['README.md']!r}"
    )
```

---

## What Makes This Impossible to Fake

1. **No DynamoDB tables** — there is no injection point for fake manifests or pre-baked TAJs
2. **`pytest.fail()` not `pytest.skip()`** — CI cannot pass silently when the real package is absent; a green run with skipped RALE tests is a broken run
3. **Exact byte assertions** — `assert body == SEED_FILES["data.csv"]` fails on any fake response, wrong file, or wrong content
4. **Hash round-trip assertion** — `assert resolved_hash == parts["hash"]` fails unless the authorizer actually called quilt3 and got back the real top hash of the seeded package
5. **`cached is False` assertion** — confirms the authorizer did NOT return a pre-seeded TAJ from anywhere

---

## Verification

```bash
# 1. Deploy (DynamoDB tables removed)
./poe deploy

# 2. Seed real package — writes .rale-test-uri automatically
python scripts/seed_packages.py

# 3. Run tests — FAIL if package missing, PASS only with real quilt3 resolution
pytest tests/integration/test_rale_end_to_end.py -v -s
```

To prove it is structurally impossible to fake, delete `.rale-test-uri`, unset
`RALE_TEST_QUILT_URI`, and run pytest. You must see:

```
FAILED tests/integration/test_rale_end_to_end.py::test_rale_authorizer_mints_taj_for_real_package
  RALE_TEST_QUILT_URI is not set and .rale-test-uri does not exist.
  Run: python scripts/seed_packages.py
```

Not a skip. A failure.
