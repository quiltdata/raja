# Local Testing Plan: Envoy JWT Authorization

## Goal

Validate the Envoy JWT + Lua authorization implementation **entirely locally** before AWS deployment. Zero cloud dependencies during development.

## Testing Strategy (TDD Approach)

### Phase 1: Write Lua Tests (RED)

### Phase 2: Implement Lua Filter (GREEN)

### Phase 3: Mock JWKS Server

### Phase 4: Docker Compose Integration

### Phase 5: End-to-End Testing

---

## Phase 1: Write Lua Tests (RED - Failing Tests)

### TDD Step 1: Write failing tests first

We'll write tests in Lua using **Busted** (Lua testing framework), testing the actual code that will run in Envoy.

### Setup Lua Testing Environment

```bash
# Install Lua and LuaRocks (package manager)
brew install lua luarocks

# Install Busted (testing framework)
luarocks install busted

# Install lua-cjson (JSON parsing, same as Envoy uses)
luarocks install lua-cjson
```

### Test File Structure

```
tests/lua/
├── authorize_spec.lua          # NEW: Test authorization logic
├── s3_request_parsing_spec.lua # NEW: Test S3 request parsing
└── prefix_matching_spec.lua    # NEW: Test prefix matching
```

### Write Lua Tests

**New file:** `tests/lua/authorize_spec.lua`

```lua
-- Tests for RAJEE authorization logic
-- Run with: busted tests/lua/

describe("S3 Request Parsing", function()
  local parse_s3_request

  before_each(function()
    -- Load the authorization module
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    parse_s3_request = auth.parse_s3_request
  end)

  describe("parse_s3_request", function()
    it("should parse GET object request", function()
      local result = parse_s3_request("GET", "/bucket/key.txt", {})
      assert.are.equal("s3:GetObject/bucket/key.txt", result)
    end)

    it("should parse GET object with nested path", function()
      local result = parse_s3_request("GET", "/bucket/uploads/user123/file.txt", {})
      assert.are.equal("s3:GetObject/bucket/uploads/user123/file.txt", result)
    end)

    it("should parse PUT object request", function()
      local result = parse_s3_request("PUT", "/bucket/key.txt", {})
      assert.are.equal("s3:PutObject/bucket/key.txt", result)
    end)

    it("should parse DELETE object request", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", {})
      assert.are.equal("s3:DeleteObject/bucket/key.txt", result)
    end)

    it("should parse HEAD object request", function()
      local result = parse_s3_request("HEAD", "/bucket/key.txt", {})
      assert.are.equal("s3:HeadObject/bucket/key.txt", result)
    end)

    it("should parse ListBucket request", function()
      local result = parse_s3_request("GET", "/bucket/", {})
      assert.are.equal("s3:ListBucket/bucket/", result)
    end)

    it("should parse InitiateMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", {uploads = ""})
      assert.are.equal("s3:InitiateMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse UploadPart", function()
      local result = parse_s3_request("PUT", "/bucket/key.txt", {uploadId = "xyz", partNumber = "1"})
      assert.are.equal("s3:UploadPart/bucket/key.txt", result)
    end)

    it("should parse CompleteMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", {uploadId = "xyz"})
      assert.are.equal("s3:CompleteMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse AbortMultipartUpload", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", {uploadId = "xyz"})
      assert.are.equal("s3:AbortMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse ListParts", function()
      local result = parse_s3_request("GET", "/bucket/key.txt", {uploadId = "xyz"})
      assert.are.equal("s3:ListParts/bucket/key.txt", result)
    end)

    it("should handle empty path", function()
      local result = parse_s3_request("GET", "/", {})
      assert.are.equal("s3:ListBucket//", result)
    end)

    it("should handle path with special characters", function()
      local result = parse_s3_request("GET", "/bucket/file%20with%20spaces.txt", {})
      assert.are.equal("s3:GetObject/bucket/file%20with%20spaces.txt", result)
    end)
  end)
end)

describe("Authorization Logic", function()
  local authorize

  before_each(function()
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    authorize = auth.authorize
  end)

  describe("authorize", function()
    it("should allow exact match", function()
      local grants = {"s3:GetObject/bucket/key.txt"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/key.txt")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched grant"))
    end)

    it("should allow prefix match", function()
      local grants = {"s3:GetObject/bucket/uploads/"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched grant"))
    end)

    it("should allow nested prefix match", function()
      local grants = {"s3:GetObject/bucket/uploads/"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/uploads/user123/file.txt")
      assert.is_true(allowed)
    end)

    it("should deny different action", function()
      local grants = {"s3:GetObject/bucket/key.txt"}
      local allowed, reason = authorize(grants, "s3:PutObject/bucket/key.txt")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no matching grant"))
    end)

    it("should deny different bucket", function()
      local grants = {"s3:GetObject/bucket1/key.txt"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket2/key.txt")
      assert.is_false(allowed)
    end)

    it("should deny shorter path", function()
      local grants = {"s3:GetObject/bucket/uploads/user123/"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/uploads/")
      assert.is_false(allowed)
    end)

    it("should allow wildcard action", function()
      local grants = {"s3:*/bucket/key.txt"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/key.txt")
      assert.is_true(allowed)
    end)

    it("should allow wildcard path", function()
      local grants = {"s3:GetObject/bucket/"}
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/any/path/file.txt")
      assert.is_true(allowed)
    end)

    it("should check multiple grants - first matches", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/"
      }
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed)
    end)

    it("should check multiple grants - second matches", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/"
      }
      local allowed, reason = authorize(grants, "s3:PutObject/bucket/docs/file.txt")
      assert.is_true(allowed)
    end)

    it("should deny when no grants match", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/"
      }
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/private/file.txt")
      assert.is_false(allowed)
    end)

    it("should deny with empty grants", function()
      local allowed, reason = authorize({}, "s3:GetObject/bucket/key.txt")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no grants"))
    end)

    it("should allow multipart workflow with wildcard", function()
      local grants = {"s3:*/bucket/large-file.bin"}

      -- Initiate
      local allowed1 = authorize(grants, "s3:InitiateMultipartUpload/bucket/large-file.bin")
      assert.is_true(allowed1)

      -- Upload part
      local allowed2 = authorize(grants, "s3:UploadPart/bucket/large-file.bin")
      assert.is_true(allowed2)

      -- Complete
      local allowed3 = authorize(grants, "s3:CompleteMultipartUpload/bucket/large-file.bin")
      assert.is_true(allowed3)
    end)

    it("should be case-sensitive", function()
      local grants = {"s3:GetObject/bucket/UPLOADS/"}
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_false(allowed)
    end)

    it("should handle grant without trailing slash", function()
      local grants = {"s3:GetObject/bucket/uploads"}
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed) -- Prefix match still works
    end)
  end)
end)

describe("Query String Parsing", function()
  local parse_query_string

  before_each(function()
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    parse_query_string = auth.parse_query_string
  end)

  it("should parse empty query string", function()
    local result = parse_query_string("")
    assert.are.same({}, result)
  end)

  it("should parse single parameter", function()
    local result = parse_query_string("uploadId=xyz")
    assert.are.equal("xyz", result.uploadId)
  end)

  it("should parse multiple parameters", function()
    local result = parse_query_string("uploadId=xyz&partNumber=1")
    assert.are.equal("xyz", result.uploadId)
    assert.are.equal("1", result.partNumber)
  end)

  it("should parse parameter with empty value", function()
    local result = parse_query_string("uploads=")
    assert.are.equal("", result.uploads)
  end)

  it("should parse parameter without value", function()
    local result = parse_query_string("uploads")
    assert.are.equal("", result.uploads)
  end)
end)
```

### Running Lua Tests (Will FAIL - no implementation yet)

```bash
# Run all Lua tests
busted tests/lua/

# Run specific test file
busted tests/lua/authorize_spec.lua

# Verbose output
busted tests/lua/ -v

# With coverage (requires luacov)
luarocks install luacov
busted tests/lua/ --coverage
luacov
```

**Expected result:** All tests FAIL because we haven't implemented the functions yet. This is correct TDD!

---

## Phase 2: Implement Lua Filter (GREEN - Make Tests Pass)

### TDD Step 2: Implement functions to make tests pass

Now we implement the actual Lua authorization logic.

### File Structure

We'll split the implementation into two files:

1. **Library file** (`authorize_lib.lua`) - Pure functions for testing
2. **Envoy filter** (`authorize.lua`) - Envoy integration that uses the library

**New file:** `infra/raja_poc/assets/envoy/authorize_lib.lua`

```lua
-- RAJEE S3 Authorization Library
-- Pure Lua functions for authorization logic (testable without Envoy)

local M = {}

-- Parse query string into table
function M.parse_query_string(query_string)
  if not query_string or query_string == "" then
    return {}
  end

  local params = {}
  for pair in string.gmatch(query_string, "[^&]+") do
    local key, value = string.match(pair, "([^=]+)=?(.*)")
    if key then
      params[key] = value or ""
    end
  end

  return params
end

-- Parse S3 request into action string
function M.parse_s3_request(method, path, query_params)
  -- Remove leading slash
  local clean_path = string.gsub(path, "^/", "")

  -- Split into bucket and key
  local bucket, key = string.match(clean_path, "([^/]+)/(.*)")
  if not bucket then
    bucket = clean_path
    key = ""
  end

  -- Determine S3 action from method and query params
  local action

  if query_params["uploads"] and not query_params["uploadId"] then
    action = "s3:InitiateMultipartUpload"
  elseif query_params["uploadId"] then
    if method == "POST" then
      action = "s3:CompleteMultipartUpload"
    elseif method == "DELETE" then
      action = "s3:AbortMultipartUpload"
    elseif method == "PUT" then
      action = "s3:UploadPart"
    else
      action = "s3:ListParts"
    end
  elseif method == "GET" and key == "" then
    action = "s3:ListBucket"
  elseif method == "GET" then
    action = "s3:GetObject"
  elseif method == "PUT" then
    action = "s3:PutObject"
  elseif method == "DELETE" then
    action = "s3:DeleteObject"
  elseif method == "HEAD" then
    action = "s3:HeadObject"
  else
    action = "s3:Unknown"
  end

  -- Construct request string
  if action == "s3:ListBucket" then
    return action .. "/" .. bucket .. "/"
  else
    return action .. "/" .. bucket .. "/" .. key
  end
end

-- Check if request matches any grant (prefix matching)
function M.authorize(grants, request_string)
  if not grants or #grants == 0 then
    return false, "no grants in token"
  end

  for _, grant in ipairs(grants) do
    -- Prefix match: request must start with grant
    if string.sub(request_string, 1, #grant) == grant then
      return true, "matched grant: " .. grant
    end
  end

  return false, "no matching grant"
end

return M
```

**New file:** `infra/raja_poc/assets/envoy/authorize.lua`

```lua
-- RAJEE Envoy Authorization Filter
-- Integrates authorize_lib with Envoy's request handling

-- Load the authorization library
local auth_lib = require("authorize_lib")

-- JSON parsing using cjson (available in Envoy Lua)
local cjson = require("cjson")

-- Main authorization handler
function envoy_on_request(request_handle)
  -- Extract method and path
  local method = request_handle:headers():get(":method")
  local path = request_handle:headers():get(":path")

  if not method or not path then
    request_handle:logErr("Missing method or path")
    request_handle:respond(
      {[":status"] = "400"},
      "Bad Request: Missing method or path"
    )
    return
  end

  -- Skip authorization for health check
  if path == "/health" then
    return
  end

  -- Extract JWT payload from header (set by jwt_authn filter)
  local jwt_payload_header = request_handle:headers():get("x-raja-jwt-payload")

  if not jwt_payload_header then
    request_handle:logWarn("Missing JWT payload header")
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Missing JWT"
    )
    return
  end

  -- Parse JWT payload
  local jwt_payload
  local success, err = pcall(function()
    jwt_payload = cjson.decode(jwt_payload_header)
  end)

  if not success then
    request_handle:logErr("Failed to parse JWT payload: " .. tostring(err))
    request_handle:respond(
      {[":status"] = "401"},
      "Unauthorized: Invalid JWT payload"
    )
    return
  end

  -- Extract grants from JWT
  local grants = jwt_payload.grants or {}

  -- Parse query parameters
  local path_parts = {}
  for part in string.gmatch(path, "[^?]+") do
    table.insert(path_parts, part)
  end

  local clean_path = path_parts[1] or path
  local query_string = path_parts[2] or ""
  local query_params = auth_lib.parse_query_string(query_string)

  -- Parse S3 request
  local request_string = auth_lib.parse_s3_request(method, clean_path, query_params)

  -- Authorize request
  local allowed, reason = auth_lib.authorize(grants, request_string)

  if allowed then
    -- Log decision and allow request
    request_handle:logInfo(string.format(
      "ALLOW: %s (reason: %s)",
      request_string,
      reason
    ))

    -- Add debug headers
    request_handle:headers():add("x-raja-decision", "allow")
    request_handle:headers():add("x-raja-reason", reason)
    request_handle:headers():add("x-raja-request", request_string)

    -- Allow request to proceed
    return
  else
    -- Log decision and deny request
    request_handle:logWarn(string.format(
      "DENY: %s (reason: %s)",
      request_string,
      reason
    ))

    -- Respond with 403
    request_handle:respond(
      {
        [":status"] = "403",
        ["x-raja-decision"] = "deny",
        ["x-raja-reason"] = reason,
        ["x-raja-request"] = request_string,
      },
      "Forbidden: " .. reason
    )
  end
end
```

### Running Lua Tests Again (Should PASS)

```bash
# Run all tests - should now pass!
busted tests/lua/ -v

# With coverage
busted tests/lua/ --coverage
luacov
cat luacov.report.out
```

**Expected result:** All tests PASS! Green ✅

### Lua Test Coverage

The `luacov` tool will show which lines of `authorize_lib.lua` are covered by tests:

```bash
luacov
cat luacov.report.out | grep authorize_lib.lua
```

Aim for 100% coverage of the authorization logic.

---

## Phase 3: Mock JWKS Server

### Goal

Run a local JWKS endpoint to simulate the control plane without AWS.

### Implementation

**New file:** `tests/local/mock_jwks_server.py`

```python
"""Mock JWKS server for local testing."""

import base64
from typing import Any

from fastapi import FastAPI
from uvicorn import run

app = FastAPI()

# Test JWT secret (same as in tests)
TEST_SECRET = "test-secret-key-for-local-testing"


@app.get("/.well-known/jwks.json")
def get_jwks() -> dict[str, Any]:
    """Return JWKS for JWT signature verification."""
    # Convert symmetric key to JWKS format
    key_bytes = TEST_SECRET.encode("utf-8")
    k_value = base64.urlsafe_b64encode(key_bytes).decode("utf-8").rstrip("=")

    return {
        "keys": [
            {
                "kty": "oct",
                "kid": "test-key-1",
                "alg": "HS256",
                "k": k_value,
            }
        ]
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    print("Starting mock JWKS server on http://localhost:8001")
    print(f"JWKS endpoint: http://localhost:8001/.well-known/jwks.json")
    run(app, host="0.0.0.0", port=8001)
```

### Helper: Generate Test Tokens

**New file:** `tests/local/generate_test_token.py`

```python
"""Generate test JWTs with grants for local testing."""

import sys
from datetime import datetime, timedelta, timezone

import jwt

TEST_SECRET = "test-secret-key-for-local-testing"


def generate_token(grants: list[str], ttl_seconds: int = 3600) -> str:
    """Generate a test JWT with grants."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl_seconds)

    payload = {
        "sub": "User::test-user",
        "iss": "https://test.local",
        "aud": ["raja-s3-proxy"],
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "grants": grants,
    }

    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
    return token


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_test_token.py <grant1> [grant2] ...")
        print("Example: python generate_test_token.py 's3:GetObject/bucket/uploads/'")
        sys.exit(1)

    grants = sys.argv[1:]
    token = generate_token(grants)

    print(f"Generated token with grants: {grants}")
    print(f"\nToken (valid for 1 hour):")
    print(token)
    print(f"\nTest with curl:")
    print(f'curl -H "Authorization: Bearer {token}" http://localhost:10000/bucket/key.txt')
```

### Usage

```bash
# Terminal 1: Start mock JWKS server
python tests/local/mock_jwks_server.py

# Terminal 2: Generate test token
python tests/local/generate_test_token.py 's3:GetObject/bucket/uploads/' 's3:PutObject/bucket/uploads/'

# Copy the token output for use in curl commands
```

---

## Phase 4: Docker Compose Integration

### Goal

Test Envoy + Lua filter locally with Docker Compose before ECS deployment.

### Docker Compose Configuration

**New file:** `infra/raja_poc/assets/envoy/docker-compose.local.yml`

```yaml
version: '3.8'

services:
  # Mock JWKS server
  jwks-server:
    build:
      context: ../../../../
      dockerfile: tests/local/Dockerfile.jwks
    ports:
      - "8001:8001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 5s
      timeout: 3s
      retries: 3
    networks:
      - rajee-local

  # Envoy proxy with authorization
  envoy:
    build:
      context: ../../../../
      dockerfile: infra/raja_poc/assets/envoy/Dockerfile
    ports:
      - "10000:10000"  # Proxy port
      - "9901:9901"    # Admin interface
    environment:
      - AUTH_DISABLED=false
      - JWKS_ENDPOINT=http://jwks-server:8001/.well-known/jwks.json
      - RAJA_ISSUER=https://test.local
      - ENVOY_LOG_LEVEL=debug
      - S3_ENDPOINT=https://s3.amazonaws.com
    depends_on:
      jwks-server:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:10000/health"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - rajee-local

networks:
  rajee-local:
    driver: bridge
```

**New file:** `tests/local/Dockerfile.jwks`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn pyjwt

# Copy mock server
COPY tests/local/mock_jwks_server.py /app/

EXPOSE 8001

CMD ["python", "mock_jwks_server.py"]
```

### Update Envoy Dockerfile

**Update:** `infra/raja_poc/assets/envoy/Dockerfile`

```dockerfile
FROM envoyproxy/envoy:v1.28-latest

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY infra/raja_poc/assets/envoy/envoy.yaml.tmpl /etc/envoy/envoy.yaml.tmpl
COPY infra/raja_poc/assets/envoy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY infra/raja_poc/assets/envoy/authorize_lib.lua /usr/local/share/lua/5.1/authorize_lib.lua
COPY infra/raja_poc/assets/envoy/authorize.lua /etc/envoy/authorize.lua

RUN chmod +x /usr/local/bin/entrypoint.sh

ENV AUTH_DISABLED=true

CMD ["/usr/local/bin/entrypoint.sh"]
```

**Note:** The `authorize_lib.lua` goes in Lua's standard library path so `require()` can find it.

---

## Phase 5: End-to-End Testing

### Local Testing Script

**New file:** `scripts/test-envoy-local.sh`

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/infra/raja_poc/assets/envoy/docker-compose.local.yml"

cd "$PROJECT_ROOT"

echo "=== RAJEE Envoy Local Testing ==="
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_test() {
    local status=$1
    local message=$2
    if [ "$status" = "PASS" ]; then
        echo -e "${GREEN}✓ $message${NC}"
    elif [ "$status" = "FAIL" ]; then
        echo -e "${RED}✗ $message${NC}"
    else
        echo -e "${YELLOW}→ $message${NC}"
    fi
}

# Cleanup function
cleanup() {
    echo
    print_test "INFO" "Cleaning up..."
    docker-compose -f "$COMPOSE_FILE" down -v
}

trap cleanup EXIT

# Build and start services
print_test "INFO" "Building Docker images..."
docker-compose -f "$COMPOSE_FILE" build

print_test "INFO" "Starting services..."
docker-compose -f "$COMPOSE_FILE" up -d

# Wait for services to be healthy
print_test "INFO" "Waiting for services to be healthy..."
for i in {1..30}; do
    if docker-compose -f "$COMPOSE_FILE" ps | grep -q "healthy"; then
        if [ $(docker-compose -f "$COMPOSE_FILE" ps | grep "healthy" | wc -l) -eq 2 ]; then
            break
        fi
    fi
    sleep 1
done

# Check if services are running
if ! docker-compose -f "$COMPOSE_FILE" ps | grep -q "Up"; then
    print_test "FAIL" "Services failed to start"
    docker-compose -f "$COMPOSE_FILE" logs
    exit 1
fi

print_test "PASS" "Services are running"
echo

# Generate test tokens
print_test "INFO" "Generating test tokens..."

# Token with uploads access
TOKEN_UPLOADS=$(python tests/local/generate_test_token.py 's3:GetObject/test-bucket/uploads/' 's3:PutObject/test-bucket/uploads/' | tail -n 2 | head -n 1)

# Token with docs access
TOKEN_DOCS=$(python tests/local/generate_test_token.py 's3:GetObject/test-bucket/docs/' | tail -n 2 | head -n 1)

# Token with wildcard access
TOKEN_ADMIN=$(python tests/local/generate_test_token.py 's3:*/test-bucket/' | tail -n 2 | head -n 1)

echo

# Test 1: Health check (no auth required)
print_test "INFO" "Test 1: Health check"
if curl -s -f http://localhost:10000/health > /dev/null; then
    print_test "PASS" "Health check succeeded"
else
    print_test "FAIL" "Health check failed"
fi
echo

# Test 2: Missing token
print_test "INFO" "Test 2: Request without token (should be 401)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:10000/test-bucket/file.txt)
if [ "$HTTP_CODE" = "401" ]; then
    print_test "PASS" "Missing token rejected with 401"
else
    print_test "FAIL" "Expected 401, got $HTTP_CODE"
fi
echo

# Test 3: Authorized request (uploads token accessing uploads)
print_test "INFO" "Test 3: Authorized request (uploads token → uploads path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN_UPLOADS" http://localhost:10000/test-bucket/uploads/file.txt)
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "403" ]; then
    # 403 from S3 (not Envoy) means auth passed
    if [ "$HTTP_CODE" = "403" ]; then
        # Check if it's from S3 or Envoy
        DECISION=$(curl -s -H "Authorization: Bearer $TOKEN_UPLOADS" http://localhost:10000/test-bucket/uploads/file.txt -D - | grep "x-raja-decision")
        if [ -n "$DECISION" ]; then
            print_test "FAIL" "Envoy denied authorized request"
        else
            print_test "PASS" "Authorized request succeeded (S3 returned 403)"
        fi
    else
        print_test "PASS" "Authorized request succeeded (HTTP $HTTP_CODE)"
    fi
else
    print_test "FAIL" "Expected 200/404/403, got $HTTP_CODE"
fi
echo

# Test 4: Unauthorized request (uploads token accessing docs)
print_test "INFO" "Test 4: Unauthorized request (uploads token → docs path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN_UPLOADS" http://localhost:10000/test-bucket/docs/file.txt)
if [ "$HTTP_CODE" = "403" ]; then
    print_test "PASS" "Unauthorized request rejected with 403"
else
    print_test "FAIL" "Expected 403, got $HTTP_CODE"
fi
echo

# Test 5: Wildcard token (admin accessing any path)
print_test "INFO" "Test 5: Wildcard token (admin → any path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN_ADMIN" http://localhost:10000/test-bucket/private/secret.txt)
if [ "$HTTP_CODE" != "403" ] || [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
    print_test "PASS" "Wildcard token succeeded (HTTP $HTTP_CODE)"
else
    print_test "FAIL" "Wildcard token was denied"
fi
echo

# Test 6: Check Envoy logs for authorization decisions
print_test "INFO" "Test 6: Checking Envoy logs for authorization decisions"
if docker-compose -f "$COMPOSE_FILE" logs envoy | grep -q "ALLOW\|DENY"; then
    print_test "PASS" "Authorization decisions logged"
    echo "Sample log entries:"
    docker-compose -f "$COMPOSE_FILE" logs envoy | grep "ALLOW\|DENY" | tail -n 3
else
    print_test "FAIL" "No authorization decisions found in logs"
fi
echo

# Test 7: Envoy admin interface
print_test "INFO" "Test 7: Envoy admin interface"
if curl -s http://localhost:9901/stats | grep -q "http"; then
    print_test "PASS" "Envoy admin interface accessible"
else
    print_test "FAIL" "Envoy admin interface not accessible"
fi
echo

# Summary
echo "=== Test Summary ==="
echo "All basic tests completed. Review output above for any failures."
echo
echo "For manual testing:"
echo "  - Envoy proxy: http://localhost:10000"
echo "  - Envoy admin: http://localhost:9901"
echo "  - JWKS server: http://localhost:8001/.well-known/jwks.json"
echo
echo "Generate custom token:"
echo "  python tests/local/generate_test_token.py 's3:GetObject/bucket/path/'"
```

Make it executable:

```bash
chmod +x scripts/test-envoy-local.sh
```

### Usage

```bash
# Run full local test suite
./scripts/test-envoy-local.sh

# Or manually control services
cd infra/raja_poc/assets/envoy
docker-compose -f docker-compose.local.yml up -d
docker-compose -f docker-compose.local.yml logs -f envoy
docker-compose -f docker-compose.local.yml down
```

---

## Complete Testing Workflow

### Step 1: Lua Unit Tests (Fastest)

```bash
# Install Lua testing tools (once)
brew install lua luarocks
luarocks install busted
luarocks install lua-cjson
luarocks install luacov

# Run tests
busted tests/lua/ -v

# With coverage
busted tests/lua/ --coverage
luacov
cat luacov.report.out
```

### Step 2: Docker Integration Tests

```bash
# Run full test suite
./scripts/test-envoy-local.sh
```

### Step 3: Manual Testing

```bash
# Start services
docker-compose -f infra/raja_poc/assets/envoy/docker-compose.local.yml up -d

# Generate token
TOKEN=$(python tests/local/generate_test_token.py 's3:GetObject/test-bucket/uploads/' | tail -n 2 | head -n 1)

# Test authorized request
curl -v -H "Authorization: Bearer $TOKEN" http://localhost:10000/test-bucket/uploads/file.txt

# Check Envoy logs
docker-compose -f infra/raja_poc/assets/envoy/docker-compose.local.yml logs -f envoy

# Cleanup
docker-compose -f infra/raja_poc/assets/envoy/docker-compose.local.yml down
```

---

## Summary

### Files Created

1. **Lua Tests:**
   - `tests/lua/authorize_spec.lua` - Comprehensive Lua tests

2. **Lua Implementation:**
   - `infra/raja_poc/assets/envoy/authorize_lib.lua` - Pure Lua logic (testable)
   - `infra/raja_poc/assets/envoy/authorize.lua` - Envoy integration

3. **Local Testing Infrastructure:**
   - `tests/local/mock_jwks_server.py` - Mock JWKS endpoint
   - `tests/local/generate_test_token.py` - Test token generator
   - `tests/local/Dockerfile.jwks` - JWKS server Docker image
   - `infra/raja_poc/assets/envoy/docker-compose.local.yml` - Local environment
   - `scripts/test-envoy-local.sh` - Automated test script

### Benefits

1. **Test actual Lua code** - No translation layer, test what runs in production
2. **Fast feedback** - Lua tests run in milliseconds
3. **No AWS dependencies** - Everything works locally
4. **Reproducible** - Docker Compose provides consistent environment
5. **Debuggable** - Easy to inspect logs and responses
6. **CI-ready** - Can run in GitHub Actions with Docker

### Testing Pyramid

```
           E2E (Docker Compose)
          /                    \
     Integration (Envoy + JWKS)
    /                             \
Unit Tests (Busted + Lua)
```

- **Unit tests** (fastest): Validate logic in isolation
- **Integration tests**: Validate Envoy + JWKS + Lua filter
- **E2E tests**: Validate full flow with real HTTP requests

### Next Steps

Once all local tests pass:

1. Deploy to AWS (following Phase 1-8 from main implementation plan)
2. Run AWS integration tests
3. Performance benchmarking
4. Production rollout

This approach ensures we validate the Lua implementation thoroughly before touching any AWS resources.
