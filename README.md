# 🔥 Distributed Rate Limiting Service

A production-grade API rate limiting service built with **FastAPI**, **Redis**, and the **Token Bucket** algorithm. Designed for real-world backend deployments with horizontal scalability, atomic operations, and zero race conditions.

---

## 🏗️ Architecture

```
Client Request
      │
      ▼
┌─────────────────────────┐
│   FastAPI Instance(s)   │  ← Horizontally scalable
│                         │
│   RateLimitMiddleware   │
│   ├─ Identify client    │  X-User-ID → user:42  /  IP fallback
│   ├─ Resolve limits     │  route → role → default priority
│   └─ Atomic Lua script  │
└────────────┬────────────┘
             │  EVALSHA (atomic)
             ▼
┌─────────────────────────┐
│         Redis           │  ← Shared state across all instances
│  Key: rl:{client}:{route}│
│  Fields: tokens, last_refill │
└─────────────────────────┘
```

---

## ⚙️ Token Bucket Algorithm

```
On each request:
  elapsed = now - last_refill
  tokens  = min(capacity, tokens + elapsed × refill_rate)

  if tokens ≥ 1  →  tokens -= 1  →  ALLOW ✅
  else           →  return retry_after  →  REJECT 429 ❌
```

---

## 🚀 Getting Started

### With Docker (recommended)

```bash
docker compose up --build
```

### Without Docker

```bash
# 1. Start Redis
redis-server

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env

# 4. Run the server
uvicorn app.main:app --reload --port 8000
```

---

## 📡 Endpoints

| Endpoint | Rate Limited | Description |
|---|---|---|
| `GET /health` | ❌ No | Liveness check + Redis ping |
| `GET /metrics` | ❌ No | Allowed/rejected counts |
| `GET /docs` | ❌ No | Swagger UI |
| `GET /api/data` | ✅ Yes | Default limit (10 burst, 1/sec) |
| `GET /api/search` | ✅ Yes | Higher limit (20 burst, 5/sec) |
| `GET /api/heavy` | ✅ Yes | Tight limit (3 burst, 0.5/sec) |
| `GET /api/admin` | ✅ Yes | Role-based (admin: 1000 burst) |

---

## 🔑 Request Headers

| Header | Purpose | Example |
|---|---|---|
| `X-User-ID` | Authenticated user identity | `42` |
| `X-User-Role` | Role for limit tier | `admin`, `premium`, `free` |

Without `X-User-ID`, the client IP is used automatically.

---

## 📊 Example Requests

### Allowed ✅
```bash
curl -i http://localhost:8000/api/data \
  -H "X-User-ID: 42" \
  -H "X-User-Role: premium"
```
```
HTTP/1.1 200 OK
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49.00

{"message": "Here is your data! 🎉", "user": "42", "role": "premium"}
```

### Rate Limited ❌
```
HTTP/1.1 429 Too Many Requests
Retry-After: 1

{"error": "Too Many Requests", "retry_after": 1, "client": "user:42"}
```

---

## ⚡ Limit Priority

```
1. Route-specific  →  /api/heavy  (3 burst, 0.5/sec)
2. Role-specific   →  admin role  (1000 burst, 100/sec)
3. Global default  →              (10 burst, 1/sec)
```

---

## 🔒 Atomicity

All Redis operations use a **Lua script via EVALSHA** — the entire read-refill-write cycle is a single atomic Redis command. No two concurrent requests can interleave and double-spend tokens.

---

## 🔥 Fail Modes

| `FAIL_OPEN` | Redis Down → | Use When |
|---|---|---|
| `true` | Allow requests | Availability is priority |
| `false` | Reject requests | Safety / cost is priority |

---

## 🧪 Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## 📁 Project Structure

```
distributed-rate-limiter/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app + routes
│   ├── middleware.py    # Rate limit middleware
│   ├── rate_limiter.py  # Token Bucket + Lua script
│   ├── metrics.py       # Request counters
│   └── config.py        # Settings (env-configurable)
├── tests/
│   ├── __init__.py
│   └── test_rate_limiter.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 📈 Scalability

- Spin up N API instances — all share one Redis
- No sticky sessions needed
- Redis Cluster compatible
- Sub-millisecond latency per rate-limit check
