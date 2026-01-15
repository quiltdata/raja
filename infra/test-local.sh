#!/bin/bash
set -e

echo "🔨 Building and starting RAJEE containers..."
docker-compose -f docker-compose.yml up -d --build

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 10

echo ""
echo "✅ Checking service health..."

echo "  • Authorizer health (FastAPI):"
curl -s http://localhost:9000/docs | head -n 1 || echo "    ❌ Authorizer not responding"

echo ""
echo "  • Envoy admin health:"
curl -s http://localhost:9901/ready || echo "    ❌ Envoy admin not ready"

echo ""
echo "  • Envoy stats:"
curl -s http://localhost:9901/stats | head -n 5

echo ""
echo "📋 Container status:"
docker-compose -f docker-compose.yml ps

echo ""
echo "📝 Recent logs:"
echo "--- Authorizer ---"
docker-compose -f docker-compose.yml logs --tail=10 authorizer

echo ""
echo "--- Envoy ---"
docker-compose -f docker-compose.yml logs --tail=10 envoy

echo ""
echo "✨ Services are running!"
echo ""
echo "Available endpoints:"
echo "  • Envoy Proxy:  http://localhost:10000"
echo "  • Envoy Admin:  http://localhost:9901"
echo "  • Authorizer:   http://localhost:9000/docs"
echo ""
echo "To view logs:    docker-compose -f docker-compose.yml logs -f"
echo "To stop:         docker-compose -f docker-compose.yml down"
