#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/infra/raja_poc/assets/envoy/docker-compose.local.yml"

cd "$PROJECT_ROOT"

echo "=== RAJEE Envoy Local Testing ==="
echo

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

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

cleanup() {
    echo
    print_test "INFO" "Cleaning up..."
    docker-compose -f "$COMPOSE_FILE" down -v
}

trap cleanup EXIT

print_test "INFO" "Building Docker images..."
docker-compose -f "$COMPOSE_FILE" build

print_test "INFO" "Starting services..."
docker-compose -f "$COMPOSE_FILE" up -d

print_test "INFO" "Waiting for services to be healthy..."
for i in {1..30}; do
    if docker-compose -f "$COMPOSE_FILE" ps | grep -q "healthy"; then
        if [ "$(docker-compose -f "$COMPOSE_FILE" ps | grep "healthy" | wc -l)" -eq 2 ]; then
            break
        fi
    fi
    sleep 1
done

if ! docker-compose -f "$COMPOSE_FILE" ps | grep -q "Up"; then
    print_test "FAIL" "Services failed to start"
    docker-compose -f "$COMPOSE_FILE" logs
    exit 1
fi

print_test "PASS" "Services are running"
echo

print_test "INFO" "Generating test tokens..."

TOKEN_UPLOADS=$(python tests/local/generate_test_token.py \
    's3:GetObject/test-bucket/uploads/' \
    's3:PutObject/test-bucket/uploads/' | tail -n 2 | head -n 1)

TOKEN_DOCS=$(python tests/local/generate_test_token.py \
    's3:GetObject/test-bucket/docs/' | tail -n 2 | head -n 1)

TOKEN_ADMIN=$(python tests/local/generate_test_token.py \
    's3:*/test-bucket/' | tail -n 2 | head -n 1)

echo

print_test "INFO" "Test 1: Health check"
if curl -s -f http://localhost:10000/health > /dev/null; then
    print_test "PASS" "Health check succeeded"
else
    print_test "FAIL" "Health check failed"
fi
echo

print_test "INFO" "Test 2: Request without token (should be 401)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:10000/test-bucket/file.txt)
if [ "$HTTP_CODE" = "401" ]; then
    print_test "PASS" "Missing token rejected with 401"
else
    print_test "FAIL" "Expected 401, got $HTTP_CODE"
fi
echo

print_test "INFO" "Test 3: Authorized request (uploads token → uploads path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN_UPLOADS" \
    http://localhost:10000/test-bucket/uploads/file.txt)
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "403" ]; then
    if [ "$HTTP_CODE" = "403" ]; then
        DECISION=$(curl -s -H "Authorization: Bearer $TOKEN_UPLOADS" \
            http://localhost:10000/test-bucket/uploads/file.txt -D - | grep "x-raja-decision")
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

print_test "INFO" "Test 4: Unauthorized request (uploads token → docs path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN_UPLOADS" \
    http://localhost:10000/test-bucket/docs/file.txt)
if [ "$HTTP_CODE" = "403" ]; then
    print_test "PASS" "Unauthorized request rejected with 403"
else
    print_test "FAIL" "Expected 403, got $HTTP_CODE"
fi
echo

print_test "INFO" "Test 5: Wildcard token (admin → any path)"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $TOKEN_ADMIN" \
    http://localhost:10000/test-bucket/private/secret.txt)
if [ "$HTTP_CODE" != "403" ] || [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
    print_test "PASS" "Wildcard token succeeded (HTTP $HTTP_CODE)"
else
    print_test "FAIL" "Wildcard token was denied"
fi
echo

print_test "INFO" "Test 6: Checking Envoy logs for authorization decisions"
if docker-compose -f "$COMPOSE_FILE" logs envoy | grep -q "ALLOW\|DENY"; then
    print_test "PASS" "Authorization decisions logged"
    echo "Sample log entries:"
    docker-compose -f "$COMPOSE_FILE" logs envoy | grep "ALLOW\|DENY" | tail -n 3
else
    print_test "FAIL" "No authorization decisions found in logs"
fi
echo

print_test "INFO" "Test 7: Envoy admin interface"
if curl -s http://localhost:9901/stats | grep -q "http"; then
    print_test "PASS" "Envoy admin interface accessible"
else
    print_test "FAIL" "Envoy admin interface not accessible"
fi
echo

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
