#!/bin/bash
set -e

COMPOSE_FILE="docker-compose.yml"

# Parse command
COMMAND="${1:-up}"

case "$COMMAND" in
  up|test)
    echo "🔨 Building and starting RAJEE containers..."
    docker-compose -f "$COMPOSE_FILE" up -d --build --remove-orphans

    echo ""
    echo "⏳ Waiting for services to be healthy..."
    for i in {1..12}; do
      unhealthy=0
      for svc in envoy; do
        cid=$(docker-compose -f "$COMPOSE_FILE" ps -q "$svc")
        status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "unknown")
        if [[ "$status" != "healthy" ]]; then
          unhealthy=1
        fi
      done
      if [[ "$unhealthy" -eq 0 ]]; then
        break
      fi
      sleep 5
    done

    echo ""
    echo "✅ Checking service health..."

    echo "  • Envoy admin health:"
    curl -s http://localhost:9901/ready || echo "    ❌ Envoy admin not ready"

    echo ""
    echo "  • Container health status:"
    health_failed=0
    for svc in envoy; do
      cid=$(docker-compose -f "$COMPOSE_FILE" ps -q "$svc")
      status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "unknown")
      echo "    - ${svc}: ${status}"
      if [[ "$status" != "healthy" ]]; then
        health_failed=1
      fi
    done

    echo ""
    echo "  • Proxy port (10000):"
    http_code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:10000/ 2>/dev/null || echo "000")
    if [[ "$http_code" != "000" ]]; then
      echo "    ✓ Port 10000 responding (HTTP $http_code)"
    else
      echo "    ❌ Port 10000 not responding"
      health_failed=1
    fi

    echo ""
    echo "  • ECS health checks (run inside containers):"
    for svc in envoy; do
      ecs_cmd="curl -f http://localhost:9901/ready"
      if docker-compose -f "$COMPOSE_FILE" exec -T "$svc" sh -c "$ecs_cmd" >/dev/null 2>&1; then
        echo "    - ${svc}: PASS (${ecs_cmd})"
      else
        echo "    - ${svc}: FAIL (${ecs_cmd})"
        health_failed=1
      fi
    done

    echo ""
    echo "  • Envoy stats:"
    curl -s http://localhost:9901/stats | head -n 5

    echo ""
    echo "📋 Container status:"
    docker-compose -f "$COMPOSE_FILE" ps

    echo ""
    echo "📝 Recent logs:"
    echo "--- Envoy ---"
    docker-compose -f "$COMPOSE_FILE" logs --tail=10 envoy

    echo ""
    echo "✨ Services are running!"
    echo ""
    echo "Available endpoints:"
    echo "  • Envoy Proxy:  http://localhost:10000"
    echo "  • Envoy Admin:  http://localhost:9901"
    echo ""
    echo "To view logs:    ./test-docker.sh logs"
    echo "To stop:         ./test-docker.sh down"

    if [[ "$health_failed" -ne 0 ]]; then
      echo ""
      echo "❌ One or more health checks failed."
      exit 1
    fi
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
    echo "  logs        Follow container logs (optionally specify service: logs envoy)"
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
