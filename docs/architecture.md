# RAJA Architecture

## System Overview

```diagram
                                              Client
                                           /          \
                               (token mgmt)            (data access)
                                    │                       │
                    ┌───────────────▼─────────-─┐   ┌───────▼───────────────────────────────────┐
                    │       Control Plane       │   │              Data Plane (RALE)            │
                    │                           │   │                                           │
                    │  ┌─────────────────────┐  │   │  ┌─────────────────────────────────────┐ │
                    │  │    API Gateway      │  │   │  │       Envoy Proxy (ALB + ECS)       │ │
                    │  │    (REST API)       │  │   │  │                                     │ │
                    │  └──────────┬──────────┘  │   │  │  ┌──────────────────────────────┐  │ │
                    │             │             │   │  │  │   Lua Filter (authorize.lua) │  │ │
                    │             ▼             │   │  │  └──────────────┬───────────────┘  │ │
                    │  ┌─────────────────────┐  │   │  │                 │                  │ │
                    │  │  Control Plane      │  │   │  │       ┌──_──────┴────────┐         │ │
                    │  │  Lambda             │  │   │  │   has TAJ?          no TAJ         │ │
                    │  │  (FastAPI + Mangum) │  │   │  │       │                 │          │ │
                    │  └──────────┬──────────┘  │   │  │       ▼                 ▼          │ │
                    │             │             │   │  │  ┌──────────┐    ┌──────────────┐  │ │
                    │             ▼             │   │  │  │  RALE    │    │    RALE      │  │ │
                    │  ┌─────────────────────┐  │   │  │  │  Router  │    │  Authorizer  │  │ │
                    │  │   Secrets Manager   │  │   │  │  │  Lambda  │    │   Lambda     │  │ │
                    │  │   (JWT sign key)    │  │   │  │  └────┬─────┘    └──────┬───────┘  │ │
                    │  └─────────────────────┘  │   │  └───────┼────────────────┼───────────┘ │
                    │             │             │   └──────────┼────────────────┼─────────────┘
                    └─────────────┼─────────────┘               │                │
                                  │                             ▼                │
                                  │                    ┌─────────────────┐       │
                                  │                    │       S3        │       │
                                  │                    │  (data objects) │       │
                                  │                    └─────────────────┘       │
                                  │                                              │
                                  │       ┌──────────────────────────────────┐  │
                                  │       │  DataZone / SageMaker Studio     │  │
                                  └──────►│                                  │◄─┘
                                          │  - subscription grants           │
                                          │  - project membership            │
                                          │  - operator portal (Studio URLs) │
                                          └──────────────────────────────────┘
```

## Request Flows

### Flow 1: RALE Bootstrap (no TAJ token)

```
 Client                  Envoy (ALB)          RALE Authorizer      DataZone
   │                         │                     Lambda              │
   │  GET /path              │                         │               │
   │  x-raja-principal: P ──►│                         │               │
   │                         │  route (no TAJ)         │               │
   │                         │────────────────────────►│               │
   │                         │                         │  membership?  │
   │                         │                         │──────────────►│
   │                         │                         │◄──────────────│
   │                         │                         │  compile TAJ  │
   │  200 + TAJ JWT ◄────────│◄────────────────────────│               │
   │                         │                         │               │
```

### Flow 2: RALE Data Request (with TAJ token)

```
 Client                  Envoy (ALB)            RALE Router           S3
   │                         │                    Lambda               │
   │  GET /logical/key       │                       │                 │
   │  x-rale-taj: <jwt> ────►│                       │                 │
   │                         │  route (has TAJ)      │                 │
   │                         │──────────────────────►│                 │
   │                         │                       │  validate JWT   │
   │                         │                       │  check manifest │
   │                         │                       │  resolve S3 key │
   │                         │                       │────────────────►│
   │                         │                       │◄────────────────│
   │  200 + object data ◄────│◄──────────────────────│  stream object  │
   │                         │                       │                 │
```

### Flow 3: RAJEE Direct (non-RALE, JWT scope checking)

```
 Client                  Envoy (ALB)                                   S3
   │                    Lua Filter                                       │
   │  GET /bucket/key        │                                          │
   │  Authorization: Bearer  │                                          │
   │  <jwt> ────────────────►│                                          │
   │                         │  1. Extract JWT payload header           │
   │                         │  2. Check audience + expiry              │
   │                         │  3. Parse S3 request → scope             │
   │                         │  4. Subset check: granted ⊇ requested   │
   │                         │                                          │
   │                    ALLOW │  add x-raja-decision: allow             │
   │                         │─────────────────────────────────────────►│
   │  200 + object ◄─────────│◄─────────────────────────────────────────│
   │                         │                                          │
   │                    DENY  │                                          │
   │  403 AccessDenied ◄─────│                                          │
```

## Component Map

```
raja/
│
├── src/raja/                   ← Pure Python library (no AWS deps)
│   ├── models.py               ← Pydantic: Scope, AuthRequest, TajToken, …
│   ├── token.py                ← JWT sign / verify (PyJWT)
│   ├── enforcer.py             ← Subset check: granted ⊇ requested
│   ├── scope.py                ← Scope parsing and wildcard expansion
│   ├── datazone/               ← DataZone membership queries
│   ├── rajee/                  ← Grant → scope compilation
│   └── rale/                   ← RALE auth logic (TAJ issuance / validation)
│
├── lambda_handlers/
│   ├── control_plane/          ← FastAPI app (Mangum) behind API Gateway
│   ├── rale_authorizer/        ← Issues TAJ JWTs (DataZone → grants → token)
│   ├── rale_router/            ← Validates TAJ, resolves USL → S3, streams
│   ├── authorizer/             ← API Gateway custom authorizer
│   └── package_resolver/       ← Quilt package resolution
│
├── infra/
│   ├── terraform/              ← All AWS resources (primary deployment path)
│   ├── envoy/                  ← Envoy Docker image + authorize.lua filter
│   └── layers/                 ← Shared Lambda layer deps
│
└── tests/
    ├── unit/                   ← Isolated, no AWS
    ├── integration/            ← Requires deployed stack
    └── hypothesis/             ← Property-based (determinism, fail-closed)
```

## Authorization Model

```
  Grant types
  ───────────

  Path grant (location-based)          Manifest grant (content-based)
  ┌──────────────────────┐             ┌──────────────────────────────┐
  │  bucket: my-bucket   │             │  pkg: owner/name@sha256:…    │
  │  prefix: /data/2024/ │             │                              │
  │  action: s3:GetObject│             │  action: quilt:ReadPackage   │
  └──────────────────────┘             └──────────────────────────────┘
           │                                         │
           │  compile (RAJEE)                        │  compile (RALE)
           ▼                                         ▼
  ┌──────────────────────┐             ┌──────────────────────────────┐
  │  JWT scope           │             │  TAJ JWT                     │
  │  s3://bucket/data/*  │             │  package_uri (immutable)     │
  │                      │             │  mode: read                  │
  │  enforced by:        │             │  exp / nbf / aud             │
  │  Envoy Lua filter    │             │                              │
  │  (subset check)      │             │  enforced by:                │
  └──────────────────────┘             │  RALE Router                 │
                                       │  (manifest membership check) │
                                       └──────────────────────────────┘
```

## Key Principles

```
  ┌─────────────────────────────────────────────────────────┐
  │  FAIL-CLOSED   unknown request → DENY (never guess)     │
  │  COMPILED      grants compiled once, enforced many times│
  │  SUBSET CHECK  enforce(granted ⊇ requested)             │
  │  TRANSPARENT   every decision includes reason + scopes  │
  │  IMMUTABLE     manifest grants can't silently expand    │
  └─────────────────────────────────────────────────────────┘
```
