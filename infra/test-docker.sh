#!/bin/bash
set -e

COMPOSE_FILE="docker-compose.yml"

# Parse command
COMMAND="${1:-up}"

case "$COMMAND" in
  up|test)
    echo "🔨 Building and starting RAJEE containers..."
    docker-compose -f "$COMPOSE_FILE" up -d --build

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
    docker-compose -f "$COMPOSE_FILE" ps

    echo ""
    echo "📝 Recent logs:"
    echo "--- Authorizer ---"
    docker-compose -f "$COMPOSE_FILE" logs --tail=10 authorizer

    echo ""
    echo "--- Envoy ---"
    docker-compose -f "$COMPOSE_FILE" logs --tail=10 envoy

    echo ""
    echo "✨ Services are running!"
    echo ""
    echo "Available endpoints:"
    echo "  • Envoy Proxy:  http://localhost:10000"
    echo "  • Envoy Admin:  http://localhost:9901"
    echo "  • Authorizer:   http://localhost:9000/docs"
    echo ""
    echo "To view logs:    ./test-docker.sh logs"
    echo "To stop:         ./test-docker.sh down"
    ;;

  logs)
    echo "📝 Following container logs (Ctrl+C to exit)..."
    docker-compose -f "$COMPOSE_FILE" logs -f "${@:2}"
    ;;

  down|stop)
    echo "🛑 Stopping RAJEE containers..."
    docker-compose -f "$COMPOSE_FILE" down
    echo "✅ Containers stopped and removed"
    ;;

  status|ps)
    docker-compose -f "$COMPOSE_FILE" ps
    ;;

  *)
    echo "Usage: $0 {up|logs|down|status}"
    echo ""
    echo "Commands:"
    echo "  up, test    Build and start containers with health checks (default)"
    echo "  logs        Follow container logs (optionally specify service: logs authorizer)"
    echo "  down, stop  Stop and remove containers"
    echo "  status, ps  Show container status"
    echo ""
    echo "Examples:"
    echo "  $0           # Start containers and run tests"
    echo "  $0 logs      # Follow all logs"
    echo "  $0 logs envoy    # Follow only Envoy logs"
    echo "  $0 down      # Stop everything"
    exit 1
    ;;
esac
