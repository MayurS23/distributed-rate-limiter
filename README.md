#  Distributed Rate Limiting Service

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.5-009688?logo=fastapi&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7.0-red?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

**A production-grade, horizontally scalable API rate limiting service built with FastAPI, Redis, and the Token Bucket algorithm. Protects backend APIs from abuse, traffic spikes, and accidental overload.**

</div>

---

##  Table of Contents

- [What is Rate Limiting?](#-what-is-rate-limiting)
- [What Does This Project Do?](#-what-does-this-project-do)
- [How It Works](#-how-it-works)
- [Architecture](#-architecture)
- [Token Bucket Algorithm](#-token-bucket-algorithm)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Installation & Setup](#-installation--setup)
  - [Option 1: Docker (Recommended)](#option-1-docker-recommended)
  - [Option 2: Manual Setup](#option-2-manual-setup)
- [API Endpoints](#-api-endpoints)
- [Request Headers](#-request-headers)
- [Example Requests & Responses](#-example-requests--responses)
- [Rate Limit Configuration](#-rate-limit-configuration)
- [Environment Variables](#-environment-variables)
- [Running Tests](#-running-tests)
- [How It Scales](#-how-it-scales)
- [Fail-Open vs Fail-Closed](#-fail-open-vs-fail-closed)
- [Design Decisions & Trade-offs](#-design-decisions--trade-offs)

---

##  What is Rate Limiting?

Rate limiting is a technique used to control how many requests a client can make to an API within a given time window. Without it:

- A single bad actor can flood your API and take it down
- Accidental infinite loops in client code can overwhelm your servers
- Traffic spikes can cause cascading failures across your backend

Rate limiting protects your service by enforcing a maximum request threshold per client, returning an `HTTP 429 Too Many Requests` response when the limit is exceeded.

---

##  What Does This Project Do?

This service acts as a **middleware layer** that sits in front of your API endpoints and:

-  Identifies each client by **User ID** (authenticated) or **IP address** (unauthenticated)
-  Tracks how many requests each client has made using a **Token Bucket** stored in Redis
-  **Allows** requests when tokens are available and forwards them to the backend
-  **Rejects** requests with `HTTP 429` when the bucket is empty
-  Supports **per-route limits** (tighter limits on expensive endpoints)
-  Supports **per-role limits** (higher limits for premium/admin users)
-  Works across **multiple API instances** using Redis as shared state
-  Handles **Redis failures** gracefully (fail-open or fail-closed mode)
-  Exposes a `/metrics` endpoint to monitor rejection rates
-  Exposes a `/health` endpoint for liveness checks

---

##  How It Works

Here is the complete request lifecycle:

```
┌─────────────────────────────────────────────────────────┐
│                        REQUEST FLOW                     │
│                                                         │
│  1. Client sends HTTP request to API                    │
│            │                                            │
│            ▼                                            │
│  2. Middleware identifies client                        │
│     → Has X-User-ID header?  → use "user:42"           │
│     → No header?             → use "ip:192.168.1.1"    │
│            │                                            │
│            ▼                                            │
│  3. Middleware resolves rate limit rules                │
│     → Route-specific rule?  → use it                   │
│     → Role-specific rule?   → use it                   │
│     → Neither?              → use global default       │
│            │                                            │
│            ▼                                            │
│  4. Atomic Lua script runs on Redis                     │
│     → Fetch current tokens + last_refill time          │
│     → Refill tokens based on elapsed time              │
│     → Check if tokens >= 1                             │
│            │                                            │
│     ┌──────┴──────┐                                     │
│     ▼             ▼                                     │
│  TOKENS > 0    TOKENS = 0                               │
│  Decrement     Return retry_after                       │
│  Allow 200 ✅  Reject 429 ❌                            │
│                                                         │
│  5. Update Redis bucket state                           │
└─────────────────────────────────────────────────────────┘
```

---

##  Architecture

```
                        ┌──────────────────┐
                        │     Clients      │
                        │  (Users / IPs)   │
                        └────────┬─────────┘
                                 │ HTTP Requests
                                 ▼
              ┌──────────────────────────────────────┐
              │         Load Balancer / Nginx         │
              └───────┬──────────────┬───────────────┘
                      │              │
             ┌────────▼───┐    ┌─────▼──────┐
             │  FastAPI   │    │  FastAPI   │   <- N instances
             │ Instance 1 │    │ Instance 2 │      (horizontal scale)
             │            │    │            │
             │ Middleware │    │ Middleware │
             └──────┬─────┘    └─────┬──────┘
                    │                │
                    │   EVALSHA (atomic Lua script)
                    └───────┬────────┘
                            │
                   ┌────────▼────────┐
                   │      Redis      │
                   │                 │
                   │  rl:user:42:/api │  <- Token bucket per client+route
                   │  tokens: 8.5    │
                   │  last_refill: t  │
                   └─────────────────┘
```

All API instances share **one Redis** — this ensures consistent rate limiting even when requests from the same client hit different servers.

---

##  Token Bucket Algorithm

The Token Bucket is the industry-standard algorithm for rate limiting. Here's how it works:

### Concept

Imagine a bucket that holds tokens:
- The bucket has a **maximum capacity** (e.g., 10 tokens)
- Tokens are **refilled automatically** at a steady rate (e.g., 1 token/second)
- Each incoming request **consumes 1 token**
- If the bucket is **empty**, the request is **rejected**

### Formula

```
elapsed_time   = current_time - last_refill_time
new_tokens     = min(capacity, current_tokens + elapsed_time x refill_rate)

if new_tokens >= 1:
    new_tokens -= 1
    ALLOW REQUEST 
else:
    retry_after = ceil((1 - new_tokens) / refill_rate)
    REJECT 429 
```

### Why Token Bucket over other algorithms?

| Algorithm | Burst Handling | Memory | Accuracy | Boundary Spikes |
|---|---|---|---|---|
| **Token Bucket**  | Smooth | Low (2 fields) | High | None |
| Fixed Window | Hard cutoff | Low | Low | Yes (edge bursts) |
| Sliding Window Log | Exact | High (log/client) | Perfect | None |
| Leaky Bucket | No burst | Low | High | None |

Token Bucket gives the **best balance** — it allows short bursts while enforcing a steady average rate, with minimal memory usage.

### Atomicity with Lua Script

To prevent race conditions when multiple API instances run concurrently, the entire token check and update happens inside a **single atomic Lua script** executed by Redis:

```
Without Lua (WRONG):           With Lua (CORRECT):
  Instance 1 reads tokens=1      Redis executes Lua atomically
  Instance 2 reads tokens=1      Instance 1 and 2 are serialized
  Instance 1 writes tokens=0     No two instances interleave 
  Instance 2 writes tokens=0  <- BOTH approved! Race condition bug
```

---

##  Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| **Python** | 3.12 | Primary language |
| **FastAPI** | 0.115.5 | Async web framework |
| **Redis** | 7.0 | Centralized token bucket store |
| **redis-py** | 5.2.0 | Async Redis client |
| **Pydantic** | 2.10.2 | Settings and data validation |
| **Uvicorn** | 0.32.1 | ASGI server |
| **Docker** | Latest | Containerization |
| **pytest** | 8.3.3 | Testing framework |

---

##  Project Structure

```
distributed-rate-limiter/
│
├── app/                          # Main application package
│   ├── __init__.py               # Package marker
│   ├── main.py                   # FastAPI app, routes, startup/shutdown
│   ├── middleware.py             # Rate limit middleware (intercepts requests)
│   ├── rate_limiter.py           # Token Bucket logic + Redis Lua script
│   ├── metrics.py                # In-process request counters
│   └── config.py                 # All settings (env-configurable)
│
├── tests/                        # Test suite
│   ├── __init__.py
│   └── test_rate_limiter.py      # Unit + integration tests
│
├── .env.example                  # Sample environment variables
├── .gitignore                    # Files excluded from git
├── Dockerfile                    # Container build instructions
├── docker-compose.yml            # Multi-service orchestration (API + Redis)
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

### What each file does

| File | Responsibility |
|---|---|
| `main.py` | Creates the FastAPI app, registers middleware, defines API routes, manages Redis lifecycle |
| `middleware.py` | Intercepts every HTTP request, identifies the client, resolves rate limits, allows or rejects |
| `rate_limiter.py` | Core token bucket logic with atomic Lua script, Redis connection pool |
| `metrics.py` | Lightweight counters tracking allowed/rejected requests per route |
| `config.py` | All configurable values (Redis host, limits, fail mode) read from environment variables |

---

##  Prerequisites

Before running this project, make sure you have the following installed:

### For Docker Setup (Recommended)

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — includes both Docker and Docker Compose
  - Windows: download and install Docker Desktop from the link above
  - Verify installation:
    ```bash
    docker --version
    docker compose version
    ```

### For Manual Setup

- [Python 3.12+](https://www.python.org/downloads/)
  ```bash
  python --version   # Should show 3.12.x or higher
  ```
- [Redis 7.0+](https://redis.io/download/)
  - Windows: use [Redis for Windows](https://github.com/microsoftarchive/redis/releases) or WSL2
  - macOS: `brew install redis`
  - Linux: `sudo apt install redis-server`
  ```bash
  redis-server --version   # Should show 7.x
  ```
- pip (comes with Python)
  ```bash
  pip --version
  ```

---

##  Installation & Setup

### Option 1: Docker (Recommended)

This is the easiest way — Docker will start both Redis and the API automatically with a single command.

**Step 1: Clone the repository**
```bash
git clone https://github.com/MayurS23/distributed-rate-limiter.git
cd distributed-rate-limiter
```

**Step 2: Start all services**
```bash
docker compose up --build
```

This single command will:
- Pull the Redis 7 Docker image
- Build the FastAPI application container
- Start Redis on port `6379`
- Start the API server on port `8000`
- Wire them together automatically

**Step 3: Verify it's running**

Open a new terminal and run:
```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "redis": "ok",
  "fail_open": true,
  "version": "1.0.0"
}
```

Or open your browser and go to: **http://localhost:8000/docs** for the interactive API explorer.

**To stop the service:**
```bash
docker compose down
```

**To run multiple API instances (simulate horizontal scaling):**
```bash
docker compose up --scale api=3
```

---

### Option 2: Manual Setup

**Step 1: Clone the repository**
```bash
git clone https://github.com/MayurS23/distributed-rate-limiter.git
cd distributed-rate-limiter
```

**Step 2: Create a virtual environment**
```bash
# Create virtual environment
python -m venv venv

# Activate it
# On Windows (Command Prompt):
venv\Scripts\activate

# On Windows (PowerShell):
venv\Scripts\Activate.ps1

# On macOS/Linux:
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt after activation.

**Step 3: Install Python dependencies**
```bash
pip install -r requirements.txt
```

**Step 4: Configure environment variables**
```bash
# Windows
copy .env.example .env

# macOS/Linux
cp .env.example .env
```

Open `.env` in any text editor — the default values work for local development without changes.

**Step 5: Start Redis**
```bash
# Start Redis server
redis-server

# In a separate terminal, verify it's running:
redis-cli ping
# Expected output: PONG
```

**Step 6: Start the API server**

Open a new terminal (with the virtual environment activated) and run:
```bash
uvicorn app.main:app --reload --port 8000
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Connecting to Redis at localhost:6379
INFO:     Redis connection OK
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Step 7: Explore the API**

Open your browser and go to **http://localhost:8000/docs** — this is the interactive Swagger UI where you can test all endpoints directly without any extra tools.

---

##  API Endpoints

### System Endpoints (Not Rate Limited)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Liveness check — returns service and Redis status |
| GET | `/metrics` | Request counters — allowed/rejected per route |
| GET | `/docs` | Interactive Swagger UI |
| GET | `/redoc` | ReDoc API documentation |

### Demo API Endpoints (Rate Limited)

| Method | Endpoint | Limit | Notes |
|---|---|---|---|
| GET | `/api/data` | 10 burst, 1/sec | Uses default global limit |
| GET | `/api/search` | 20 burst, 5/sec | Route-specific higher limit |
| GET | `/api/heavy` | 3 burst, 0.5/sec | Route-specific tighter limit |
| GET | `/api/admin` | 1000 burst, 100/sec | Requires `X-User-Role: admin` |

---

##  Request Headers

### Headers You Can Send

| Header | Required | Description | Example |
|---|---|---|---|
| `X-User-ID` | Optional | Authenticated user identity. If absent, client IP is used instead. | `42` |
| `X-User-Role` | Optional | User role for tiered limits. Defaults to `free`. | `admin`, `premium`, `free` |

### Headers Returned in Every Response

| Header | Description | Example |
|---|---|---|
| `X-RateLimit-Limit` | Total bucket capacity | `10` |
| `X-RateLimit-Remaining` | Tokens remaining after this request | `8.00` |
| `X-RateLimit-Policy` | Which rule was applied | `capacity=10;rate=1.0/s` |
| `Retry-After` | Seconds to wait before retrying (only on 429) | `1` |

---

##  Example Requests & Responses

### 1. Allowed Request 

```bash
curl -i http://localhost:8000/api/data \
  -H "X-User-ID: 42" \
  -H "X-User-Role: free"
```

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9.00
X-RateLimit-Policy: capacity=10;rate=1.0/s

{
  "message": "Here is your data! ",
  "user": "42",
  "role": "free"
}
```

### 2. Rate Limited Request 

After sending more than 10 requests in a short time:

```http
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0.00
Retry-After: 1

{
  "error": "Too Many Requests",
  "message": "Rate limit exceeded. Retry after 1s.",
  "retry_after": 1,
  "client": "user:42"
}
```

### 3. Premium User (Higher Limits)

```bash
curl -i http://localhost:8000/api/data \
  -H "X-User-ID: 99" \
  -H "X-User-Role: premium"
```

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49.00
X-RateLimit-Policy: capacity=50;rate=10.0/s
```

### 4. Unauthenticated Request (IP-based limiting)

```bash
curl -i http://localhost:8000/api/data
```

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9.00
X-RateLimit-Policy: capacity=10;rate=1.0/s

{
  "message": "Here is your data! ",
  "user": "anonymous",
  "role": "free"
}
```

### 5. Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "redis": "ok",
  "fail_open": true,
  "version": "1.0.0"
}
```

### 6. Metrics

```bash
curl http://localhost:8000/metrics
```

```json
{
  "uptime_seconds": 142.3,
  "total_allowed": 1820,
  "total_rejected": 47,
  "rejection_rate": 2.52,
  "by_route": {
    "/api/data":   { "allowed": 1200, "rejected": 40 },
    "/api/search": { "allowed": 620,  "rejected": 7  }
  },
  "recent_rejections": [
    { "client": "user:42", "route": "/api/data", "ts": 1710000000.0 }
  ]
}
```

---

##  Rate Limit Configuration

Limits are resolved in this priority order (highest wins):

```
1. Route-specific  →  /api/heavy has its own tight rule
2. Role-specific   →  admin role gets high limits
3. Global default  →  everyone else gets default
```

### Default Global Limit

| Capacity (Burst) | Refill Rate |
|---|---|
| 10 requests | 1 request/second |

### Route-Specific Limits

| Route | Capacity | Refill Rate | Reason |
|---|---|---|---|
| `/api/heavy` | 3 | 0.5/sec | Expensive computation |
| `/api/search` | 20 | 5/sec | Read-heavy, can allow more |

### Role-Based Limits

| Role | Capacity | Refill Rate |
|---|---|---|
| `free` | 5 | 0.5/sec |
| `premium` | 50 | 10/sec |
| `admin` | 1000 | 100/sec |

All of these are fully configurable in `app/config.py`.

---

##  Environment Variables

Copy `.env.example` to `.env` to configure the service:

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis server hostname |
| `REDIS_PORT` | `6379` | Redis server port |
| `REDIS_DB` | `0` | Redis database index |
| `REDIS_PASSWORD` | _(empty)_ | Redis password (leave empty if none) |
| `DEFAULT_CAPACITY` | `10` | Default token bucket burst capacity |
| `DEFAULT_REFILL_RATE` | `1.0` | Default tokens added per second |
| `FAIL_OPEN` | `true` | Behavior when Redis is unavailable (see below) |
| `BUCKET_TTL` | `3600` | Seconds before an inactive bucket is deleted |

---

##  Running Tests

The test suite covers token bucket logic, middleware behavior, and metrics. It uses an in-memory fake Redis so **no real Redis is needed** to run tests.

**Run all tests:**
```bash
pytest tests/ -v
```

**Expected output:**
```
tests/test_rate_limiter.py::TestTokenBucket::test_first_request_allowed        PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_burst_exhaustion             PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_refill_over_time             PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_different_clients_isolated   PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_different_routes_isolated    PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_fail_open_on_redis_error     PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_fail_closed_on_redis_error   PASSED
tests/test_rate_limiter.py::TestRateLimitMiddleware::test_health_endpoint_exempt     PASSED
tests/test_rate_limiter.py::TestRateLimitMiddleware::test_rejected_request_returns_429 PASSED
tests/test_rate_limiter.py::TestMetrics::test_record_allowed                   PASSED
tests/test_rate_limiter.py::TestMetrics::test_record_rejected                  PASSED
tests/test_rate_limiter.py::TestMetrics::test_rejection_rate                   PASSED

12 passed in 1.23s
```

---

##  How It Scales

### Horizontal Scaling

Run any number of API instances — all share the same Redis:

```bash
docker compose up --scale api=3
```

All 3 instances read and write to the same Redis bucket state. A request from `user:42` hitting Instance 1 and then Instance 2 sees the correct token count because Redis is the **single source of truth**.

### Why This Works

- **Stateless API instances** — no in-memory state, each instance is identical
- **No sticky sessions needed** — any instance can handle any client request
- **Atomic operations** — Lua script prevents race conditions between instances
- **Auto-cleanup** — Redis TTL expires stale buckets after 1 hour automatically

### Performance Numbers

| Metric | Value |
|---|---|
| Rate check latency (local Redis) | < 1ms |
| Rate check latency (cloud, same region) | < 5ms |
| Redis memory per active bucket | ~100 bytes |
| 1 million active users | ~100MB Redis memory |

---

##  Fail-Open vs Fail-Closed

If Redis becomes unavailable, you have two options configured via `FAIL_OPEN`:

| Mode | Setting | Redis Down Behavior | Use When |
|---|---|---|---|
| **Fail-Open** | `FAIL_OPEN=true` _(default)_ |  Allow all requests through | Availability is the priority |
| **Fail-Closed** | `FAIL_OPEN=false` |  Reject all requests with 429 | Security or cost is the priority |

---

##  Design Decisions & Trade-offs

### Why FastAPI?
- Native async/await support — handles thousands of concurrent requests without blocking
- Auto-generates Swagger UI at `/docs` — no extra documentation work
- Clean Starlette middleware system — easy to intercept all requests in one place

### Why Redis?
- Sub-millisecond read/write latency
- Native Lua scripting for atomic multi-step operations
- Automatic key expiration (TTL) handles cleanup with zero extra work
- Industry standard for distributed rate limiting state

### Why Token Bucket?
- Uses only 2 fields per client (`tokens` + `timestamp`) — extremely memory efficient
- Naturally handles burst traffic without complex logic
- Easy to reason about and explain: "you get N tokens, they refill at R per second"

### Trade-offs

| Decision | Advantage | Trade-off |
|---|---|---|
| Redis as shared store | Consistent across all instances | Redis becomes a dependency (mitigated by fail-open mode) |
| Lua script atomicity | Zero race conditions | Slightly more complex than simple GET/SET |
| In-process metrics | Zero performance overhead | Metrics reset on restart — use Prometheus in production |
| Header-based User ID | Simple and flexible | Requires a trusted reverse proxy in production to prevent header spoofing |

---

## 📄 License

This project is open source and available under the [MIT License](LICENSE).

---

<div align="center">
Built with ❤️ by <a href="https://github.com/MayurS23">MayurS23</a>
</div>
