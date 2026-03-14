# RAJA

This README is intentionally short and focused on day-to-day usage.

For architecture, design notes, tests, and deeper docs, see [AGENTS.md](AGENTS.md).

## Target Workflow

1. Set env and deploy the stack.
2. Use the Admin UI and/or call RALE via `boto3`.
3. Add S3 buckets for testing.

## 1) Set Env And Deploy

Prereqs:
- AWS credentials configured locally
- `uv`, `terraform`, `docker`

```bash
uv sync

# one-time (if missing)
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars

# required admin key used by protected control-plane endpoints
cat > .env <<'ENV'
RAJA_ADMIN_KEY=change-me-admin-key
RAJA_USERS=ernest-staging,kevin-staging,simon-staging
ENV

./poe deploy
python scripts/show_outputs.py
```

`./poe deploy` writes deployment outputs to `infra/tf-outputs.json`.

## 2) Run Admin UI

```bash
export API_URL="$(python - <<'PY'
import json
print(json.load(open('infra/tf-outputs.json'))['api_url'])
PY
)"

open "$API_URL"
```

- Browse to `/` for the Admin UI.
- Enter the same `RAJA_ADMIN_KEY` you used for deploy.
- The Token and Enforcement forms default to the first `RAJA_USERS` entry from `.env`.

Quick API check:

```bash
curl -sS "$API_URL/principals" \
  -H "Authorization: Bearer $RAJA_ADMIN_KEY"
```

## 3) Call RALE With boto3

This uses the RAJEE endpoint (which fronts RALE) with normal S3 API calls.

```bash
export API_URL="$(python - <<'PY'
import json
o=json.load(open('infra/tf-outputs.json'))
print(o['api_url'])
PY
)"
export RAJEE_ENDPOINT="$(python - <<'PY'
import json
o=json.load(open('infra/tf-outputs.json'))
print(o['rajee_endpoint'])
PY
)"
export TEST_BUCKET="$(python - <<'PY'
import json
o=json.load(open('infra/tf-outputs.json'))
print(o['rajee_test_bucket_name'])
PY
)"

# create a principal with test-bucket permissions
export DEMO_PRINCIPAL="$(python - <<'PY'
import os
import boto3

users = [u.strip() for u in os.environ["RAJA_USERS"].split(",") if u.strip()]
account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"arn:aws:iam::{account_id}:user/{users[0]}")
PY
)"

curl -sS -X POST "$API_URL/principals" \
  -H "Authorization: Bearer $RAJA_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"principal\":\"${DEMO_PRINCIPAL}\",\"scopes\":[\"S3Object:${TEST_BUCKET}/*:s3:GetObject\",\"S3Object:${TEST_BUCKET}/*:s3:PutObject\",\"S3Bucket:${TEST_BUCKET}:s3:ListBucket\"]}"

# mint a RAJEE token for that principal
export RAJEE_TOKEN="$(curl -sS -X POST "$API_URL/token" \
  -H "Authorization: Bearer $RAJA_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"principal\":\"${DEMO_PRINCIPAL}\",\"token_type\":\"rajee\"}" | python -c 'import sys,json; print(json.load(sys.stdin)["token"])')"
```

```python
import os
import boto3
from botocore.config import Config

region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
endpoint = os.environ["RAJEE_ENDPOINT"]
token = os.environ["RAJEE_TOKEN"]
bucket = os.environ["TEST_BUCKET"]

s3 = boto3.client(
    "s3",
    endpoint_url=endpoint,
    region_name=region,
    config=Config(s3={"addressing_style": "path"}),
)

def _headers(request, **_):
    request.headers["Host"] = f"s3.{region}.amazonaws.com"
    request.headers["x-raja-authorization"] = f"Bearer {token}"

s3.meta.events.register("before-sign.s3", _headers)

s3.put_object(Bucket=bucket, Key="rajee-integration/hello.txt", Body=b"hello")
print(s3.get_object(Bucket=bucket, Key="rajee-integration/hello.txt")["Body"].read())
print([x["Key"] for x in s3.list_objects_v2(Bucket=bucket, Prefix="rajee-integration/").get("Contents", [])])
```

## 4) Add Buckets To Test With

1. Add a new `aws_s3_bucket` (+ versioning/encryption/public-access-block) in `infra/terraform/main.tf`.
2. Add that bucket ARN to both IAM policies in `infra/terraform/main.tf`:
   - `aws_iam_role_policy.rale_router_permissions`
   - `aws_iam_role_policy.rajee_task_permissions`
3. Add an output in `infra/terraform/outputs.tf` if you want the bucket name in `infra/tf-outputs.json`.
4. Re-deploy:

```bash
./poe deploy
```
