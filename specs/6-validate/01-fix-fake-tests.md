# Fix the Fake Tests: Remove DynamoDB from RALE, Use Real Packages

## The Problem in One Sentence

Every RALE test pre-seeds DynamoDB with fake data instead of using real Quilt packages,
which means the system has never been proven to work.

**The rule: only real packages. No fake anything.**

---

## Root Cause: DynamoDB is a Lie

The RALE stack has two DynamoDB tables:

- `taj_cache` — caches TAJ tokens in the authorizer
- `manifest_cache` — caches package manifests in the router AND stores "latest pointer" records

Both tables exist for performance. For a POC, that performance is not needed.
What the performance cache buys us is: **the ability to fake the tests by seeding
fake data instead of exercising real package resolution.**

Remove the tables. Force every request to go through quilt3. The tests cannot lie
if there's nothing to pre-seed.

---

## Changes

| File | Change |
| --- | --- |
| `infra/terraform/main.tf` | delete `manifest_cache` and `taj_cache` DynamoDB tables |
| `infra/terraform/outputs.tf` | delete `manifest_cache_table_name` and `taj_cache_table_name` outputs |
| `lambda_handlers/rale_authorizer/handler.py` | remove DynamoDB cache; call quilt3 directly to resolve latest hash |
| `lambda_handlers/rale_router/handler.py` | remove DynamoDB cache; call quilt3 directly every request |
| `tests/integration/helpers.py` | remove `require_taj_cache_table`, `require_manifest_cache_table`; add `require_rale_test_quilt_uri` |
| `tests/integration/test_rale_end_to_end.py` | rewrite all tests to use real Quilt URIs, no DynamoDB seeding |
| `scripts/seed_packages.py` | print `export RALE_TEST_QUILT_URI=...` after push |

---

## What the Tests Look Like After

```python
@pytest.mark.integration
def test_rale_authorizer_mints_taj_for_real_package():
    uri = require_rale_test_quilt_uri()  # quilt+s3://registry#package=demo/package-grant@hash
    # call authorizer with a USL derived from uri
    # assert status 200, taj returned
    # NO DynamoDB setup, NO fake data

@pytest.mark.integration
def test_rale_router_fetches_real_s3_object():
    uri = require_rale_test_quilt_uri()
    taj = <mint from authorizer>
    # call router with taj + USL
    # assert status 200, body == real file content
    # NO DynamoDB setup, NO fake data
```

---

## Verification

```bash
# 1. Deploy (with DynamoDB tables removed)
./poe deploy

# 2. Push real test package
python scripts/seed_packages.py
export RALE_TEST_QUILT_URI=<printed value>

# 3. Run tests — all real, nothing faked
pytest tests/integration/test_rale_end_to_end.py -v -s
```
