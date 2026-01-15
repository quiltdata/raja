# Local Testing for RAJEE Envoy Stack

This directory contains tools for testing the RAJEE Envoy proxy and authorizer containers locally before deploying to AWS.

## Quick Start

```bash
# From repo root
./poe test-docker

# Or directly from infra directory
cd infra
./test-docker.sh
```

This script will:
1. Build both Docker containers (Envoy + Authorizer)
2. Start them with docker-compose
3. Wait for health checks to pass
4. Display service status and logs
5. Show available endpoints

### Commands

```bash
# Start containers (default)
./poe test-docker
./test-docker.sh up

# View logs
ARGS=logs ./poe test-docker
./test-docker.sh logs
./test-docker.sh logs envoy      # Only Envoy logs
./test-docker.sh logs authorizer # Only authorizer logs

# Stop containers
ARGS=down ./poe test-docker
./test-docker.sh down

# Check status
./test-docker.sh status
```

## Manual Testing

### Start Services

```bash
docker-compose up -d --build
```

### Check Health

```bash
# Authorizer API docs (should return HTML)
curl http://localhost:9000/docs

# Envoy admin ready endpoint (should return "LIVE")
curl http://localhost:9901/ready

# Envoy stats
curl http://localhost:9901/stats
```

### View Logs

```bash
# Follow all logs
docker-compose logs -f

# Just authorizer
docker-compose logs -f authorizer

# Just envoy
docker-compose logs -f envoy
```

### Test Authorization Flow

```bash
# This should hit Envoy -> ext_authz (authorizer) -> S3
# Will likely fail authorization since we don't have a valid token
curl -v http://localhost:10000/
```

### Stop Services

```bash
docker-compose down
```

## Architecture

```
┌─────────────────────────────────────────┐
│  Docker Compose Network (rajee-net)     │
│                                         │
│  ┌──────────────┐    ┌──────────────┐  │
│  │ Authorizer   │    │   Envoy      │  │
│  │ (FastAPI)    │◄───│   Proxy      │  │
│  │              │    │              │  │
│  │ Port: 9000   │    │ Port: 10000  │  │
│  └──────────────┘    │ Admin: 9901  │  │
│                      └──────────────┘  │
└─────────────────────────────────────────┘
           ▲
           │
     localhost:10000
```

## Health Checks

Both containers have health checks configured:

### Authorizer
- **Endpoint:** `GET /docs`
- **Interval:** 10s
- **Start Period:** 10s

### Envoy
- **Endpoint:** `GET /ready` (admin port 9901)
- **Interval:** 10s
- **Start Period:** 30s (Envoy takes longer to initialize)

These match the ECS health checks defined in the CDK stack.

## Troubleshooting

### Containers keep restarting

Check logs:
```bash
docker-compose logs --tail=50
```

### Health checks failing

Verify endpoints manually:
```bash
# Should return LIVE
curl http://localhost:9901/ready

# Should return HTML
curl http://localhost:9000/docs
```

### Port conflicts

If ports are already in use, modify `docker-compose.yml` to use different host ports.

## Differences from AWS Deployment

1. **JWT Secret:** Uses hardcoded test secret instead of AWS Secrets Manager
2. **S3 Access:** Won't have IAM role credentials for S3 access
3. **Networking:** Uses Docker bridge network instead of VPC
4. **Load Balancer:** No ALB in front of Envoy

Despite these differences, this setup validates:
- Container build processes
- Health check endpoints
- Inter-container communication
- Envoy configuration
- Authorizer FastAPI app startup
