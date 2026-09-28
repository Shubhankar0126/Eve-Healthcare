# EVE Diagnostics Booking API

A backend service for **diagnostic-test bookings and simulated payments**, built for the EVE Healthcare SDE Intern assignment.
Users sign up, browse centres and the tests they offer, book an appointment, pay through a mock payment service, and a
signed, **idempotent webhook** finalises the outcome.

**Stack:** FastAPI · SQLAlchemy 2 · PostgreSQL · Alembic · JWT · Redis · Celery · Docker · pytest

> **61 tests** (API on SQLite, plus Redis and PostgreSQL integration tests) · verified end-to-end with a real
> API + Celery worker + Redis + PostgreSQL.

---

## Table of contents
1. [Features](#features) · 2. [Architecture](#architecture) · 3. [Quick start](#quick-start) · 4. [Configuration](#configuration)
5. [API reference](#api-reference) · 6. [Payment & webhook flow](#payment--webhook-flow) · 7. [Database design](#database-design)
8. [Background jobs](#background-jobs) · 9. [Edge cases handled](#edge-cases-handled) · 10. [Testing](#testing)
11. [Design decisions](#design-decisions) · 12. [Assumptions](#assumptions) · 13. [Improvements](#what-id-improve-with-more-time)
14. [Project layout](#project-layout) · 15. [Git workflow](#git-workflow)

---

## Features

| Required | Bonus (all implemented) |
|---|---|
| Signup / login, JWT, request validation | **Redis caching** (catalogue reads, version-based invalidation, graceful fallback) |
| Centres + tests + per-centre price | **Celery** worker + beat (confirmation notifications, stale-payment expiry, mock-provider callbacks) |
| Bookings with status (`PENDING/CONFIRMED/FAILED/CANCELLED`) | **Webhook retry handling** – exponential backoff + jitter, transactional rollback on failure |
| Mock `POST /payments/` → `SUCCESS` / `FAILED` | **Docker & docker-compose** (api, worker, beat, Postgres, Redis, healthchecks) |
| `POST /payments/webhook/`, **idempotent** | **Swagger / OpenAPI** at `/docs` (with working *Authorize*) |
| Edge-case handling | **Tests** – 61, incl. duplicate/concurrent webhook delivery, Redis, migrations |
| | **Structured JSON logging** with `X-Request-ID` correlation |
| | **Pagination**, **rate limiting** (Redis-backed, in-memory fallback) |
| | **Alembic migrations** (checked against the models for drift) · **GitHub Actions CI** |

Beyond the brief: HMAC-signed webhooks, `Idempotency-Key` on payments, a DB-level guard against duplicate slot bookings,
explicit state machines, server-side pricing with a price snapshot, and a `/health` endpoint.

## Architecture

```mermaid
flowchart LR
  C[Client] -->|JWT| API[FastAPI API]
  API --> PG[(PostgreSQL)]
  API <-->|cache + rate limits| R[(Redis)]
  API -->|enqueue| R
  R --> W[Celery worker]
  B[Celery beat] -->|every 60s| R
  W --> PG
  W -->|signed webhook, retried with backoff| API
```

* **Routers** (`app/routers`) only do HTTP: parsing, auth, status codes.
* **Services** (`app/services`) hold business rules and state transitions, and are the only place that mutates bookings/payments.
* **Worker** (`app/worker`) runs background jobs; it reuses the same services, so there is one implementation of each rule.
* Redis is **optional**: without it the API still works (no cache, in-memory rate limiting, tasks use an in-process broker).

## Quick start

### Docker (recommended)
```bash
docker compose up --build
```
Starts Postgres, Redis, the API (runs `alembic upgrade head` and seeds demo data), a Celery worker and Celery beat.

* Swagger UI: http://localhost:8000/docs · Health: http://localhost:8000/health
* Seeded admin: `admin@example.com` / `Admin12345` (change via `ADMIN_EMAIL` / `ADMIN_PASSWORD`)

### Local
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env               # set DATABASE_URL (PostgreSQL); REDIS_URL is optional
alembic upgrade head               # create the schema
python -m app.seed                 # admin user + 3 centres + 6 tests (idempotent)
uvicorn app.main:app --reload
# optional, in other terminals (needs Redis):
celery -A app.worker.celery_app worker -l info
celery -A app.worker.celery_app beat -l info
```
`make up | migrate | seed | run | worker | beat | test` are shortcuts for the above.

### Try it in 60 seconds
```bash
B=http://localhost:8000
curl -s $B/auth/signup -H 'content-type: application/json' \
  -d '{"email":"me@example.com","full_name":"Me Myself","password":"Passw0rd123"}'
TOKEN=$(curl -s $B/auth/login -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"Passw0rd123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

curl -s "$B/centres?city=Indore"                                   # public catalogue with prices
curl -s $B/bookings -H "$AUTH" -H 'content-type: application/json' \
  -d '{"test_id":1,"centre_id":3,"appointment_at":"2026-12-01T10:00:00+05:30"}'
curl -s $B/payments/ -H "$AUTH" -H 'content-type: application/json' \
  -H 'Idempotency-Key: order-1' -d '{"booking_id":1}'               # random SUCCESS / FAILED
```
**Async payment** – with docker-compose the mock provider calls the webhook itself (via the worker):
```bash
curl -s $B/payments/ -H "$AUTH" -H 'content-type: application/json' \
  -d '{"booking_id":1,"simulate_outcome":"PENDING"}'                # status PENDING
sleep 5; curl -s $B/bookings/1 -H "$AUTH"                            # -> "status": "CONFIRMED" (or FAILED)
```
**Call the webhook by hand** (signature = hex HMAC-SHA256 of the raw body):
```bash
BODY='{"event_id":"evt_1","type":"payment.succeeded","data":{"payment_reference":"pay_..."}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac whsec_dev | awk '{print $NF}')
curl -s $B/payments/webhook/ -H "X-Signature: $SIG" -H 'content-type: application/json' -d "$BODY"
# run it again -> {"status":"duplicate"}; nothing changes
```

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://eve:eve@localhost:5432/eve` | PostgreSQL connection |
| `JWT_SECRET` | dev value | **Change in production** (≥ 32 bytes) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT lifetime |
| `WEBHOOK_SECRET` | `whsec_dev` | HMAC key shared with the payment provider |
| `REDIS_URL` | *(empty)* | Enables cache, Redis rate limiting and the Celery broker |
| `RATE_LIMIT_ENABLED`, `AUTH_RATE_LIMIT_PER_MINUTE`, `PAYMENT_RATE_LIMIT_PER_MINUTE` | `true`, `10`, `30` | Per-IP limits |
| `PENDING_PAYMENT_TIMEOUT_MINUTES` | `15` | Beat job fails payments the provider never resolved |
| `SIMULATE_PROVIDER_CALLBACK` | `false` | Mock provider POSTs the webhook for `PENDING` payments (needs a worker) |
| `WEBHOOK_TARGET_URL` | `http://localhost:8000/payments/webhook/` | Where the mock provider delivers |
| `LOG_LEVEL` | `INFO` | JSON logs to stdout |

## API reference

Interactive docs: **`/docs`** (Swagger) and `/redoc`. Errors are always `{"detail": "...", "code": "machine_readable"}`;
request-validation errors keep FastAPI's standard `422` shape.

| Method & path | Auth | Description |
|---|---|---|
| `POST /auth/signup` | – | email (unique, case-insensitive), name, password (8–64 chars, letter + digit) |
| `POST /auth/login` | – | JSON credentials → JWT. `POST /auth/token` is the form variant behind Swagger's *Authorize* |
| `GET /auth/me` | user | current user |
| `GET /tests` · `GET /centres` · `GET /centres/{id}` | – | paginated (`page`, `page_size ≤ 100`); filters `city`, `test_id`, `q` |
| `POST /tests` · `POST /centres` | admin | create catalogue entries |
| `PUT /centres/{id}/tests/{test_id}` · `DELETE …` | admin | offer a test at a centre / change price / stop offering (soft delete) |
| `POST /bookings` | user | body `{test_id, centre_id, appointment_at}`; **amount is set by the server** |
| `GET /bookings` · `GET /bookings/{id}` | user | own bookings only; `?status=` filter |
| `POST /bookings/{id}/cancel` | user | |
| `POST /payments/` | user | `{booking_id, simulate_outcome?}` + optional `Idempotency-Key` header |
| `GET /payments/` · `GET /payments/{id}` | user | own payments only |
| `POST /payments/webhook/` | HMAC | header `X-Signature`; see below |
| `GET /health` | – | DB and cache status |

<details><summary>Example: create booking → response</summary>

```json
{
  "id": 1, "user_id": 2, "test_id": 1, "test_name": "Complete Blood Count",
  "centre_id": 3, "centre_name": "EVE Diagnostics Vijay Nagar",
  "appointment_at": "2026-12-01T04:30:00Z", "amount": "450.00",
  "status": "PENDING", "created_at": "…", "updated_at": "…"
}
```
</details>

## Payment & webhook flow

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant DB as PostgreSQL
  participant Q as Celery worker (mock provider)
  U->>A: POST /payments/ {booking_id, PENDING}
  A->>DB: lock booking, insert payment (PENDING)
  A-->>U: 201 payment PENDING
  A->>Q: enqueue send_provider_webhook (countdown)
  Q->>A: POST /payments/webhook/ (HMAC-signed) - retried with backoff on 5xx/network errors
  A->>DB: one transaction: insert event ledger row, lock payment+booking, update, commit
  A-->>Q: 200 processed
  A->>Q: enqueue booking confirmation notification
  Q->>A: redelivery of the same event_id
  A-->>Q: 200 duplicate (no state change)
```

**State machines**
```
Booking : PENDING → CONFIRMED | FAILED | CANCELLED     FAILED → CONFIRMED (payment retry) | CANCELLED
          CONFIRMED → CANCELLED                        CANCELLED is terminal
Payment : PENDING → SUCCESS | FAILED                   SUCCESS / FAILED are terminal (immutable)
```

**Why the webhook is idempotent**
1. The HMAC is verified over the **raw body** with a constant-time compare (401 if wrong) *before* anything is parsed; then the payload is validated (422).
2. In **one transaction** the handler inserts the event into `webhook_events` (`UNIQUE(event_id)`), locks the payment and booking rows (`SELECT … FOR UPDATE`), applies the change and commits.
3. A redelivery violates the unique constraint → `200 {"status":"duplicate"}`; no payment or booking is touched. Concurrent duplicates block on the unique index until the first commits, then resolve as duplicates. *Verified on PostgreSQL: 20 simultaneous identical requests → 1 `processed`, 19 `duplicate`, one payment, one confirmed booking.*
4. If processing fails midway the ledger row rolls back too, so the provider's retry is handled from scratch. An unknown `payment_reference` returns 404 **without** recording the event, so a retry works once the payment exists.
5. A *different* event that contradicts a terminal payment (e.g. `failed` after `succeeded`) is stored as `IGNORED` and acknowledged; the terminal state never flips.

## Database design

```mermaid
erDiagram
  users ||--o{ bookings : makes
  users ||--o{ payments : makes
  centres ||--o{ centre_tests : offers
  tests ||--o{ centre_tests : "offered as"
  centres ||--o{ bookings : hosts
  tests ||--o{ bookings : "booked for"
  bookings ||--o{ payments : "paid by"
```

| Table | Key columns / constraints |
|---|---|
| `users` | `email` UNIQUE, `hashed_password` (bcrypt), `role` |
| `tests` | `code` UNIQUE, `name` |
| `centres` | `name`, `city` (indexed), `address` |
| `centre_tests` | `centre_id`, `test_id`, **`price`**, `is_active`; `UNIQUE(centre_id, test_id)` |
| `bookings` | `user_id`, `test_id`, `centre_id`, `appointment_at`, **`amount`** (snapshot), `status`; partial unique index on active slots |
| `payments` | `booking_id`, `amount`, `status`, `provider_reference` UNIQUE, `idempotency_key`; `UNIQUE(user_id, idempotency_key)` |
| `webhook_events` | `event_id` UNIQUE (idempotency ledger), `payload` JSONB, `status` |

* **Price is per centre**, and `bookings.amount` is a **snapshot**, so later price changes never rewrite history.
* Money is `NUMERIC(10,2)` / `Decimal`, never float.
* **Partial unique index** `uq_booking_active_slot (user, test, centre, appointment_at) WHERE status <> 'CANCELLED'` stops duplicate bookings even under races; cancelling frees the slot.
* Enums are stored as `VARCHAR`, not native PG enums, so adding a state doesn't need `ALTER TYPE`.
* Schema is owned by **Alembic** (`alembic/versions/0001_initial_schema.py`); a test asserts that migrations produce exactly the schema the models describe and that `downgrade` is clean.

## Background jobs

| Task | Trigger | What it does |
|---|---|---|
| `send_provider_webhook` | payment created as `PENDING` (when `SIMULATE_PROVIDER_CALLBACK=true`) | Mock provider posts a signed webhook. **Retries** on network errors, 5xx, 408, 429 and 404 with exponential backoff + jitter (up to 6 retries, max 5 min). Other 4xx are not retried. The `event_id` is deterministic, so retries are collapsed by the ledger. |
| `expire_stale_payments` | Celery beat, every 60 s | Fails `PENDING` payments older than `PENDING_PAYMENT_TIMEOUT_MINUTES` (and their bookings) so nothing hangs forever. Re-checks each row under a lock, so it can't race a webhook. |
| `send_booking_confirmation` | booking becomes `CONFIRMED` | Simulated email/SMS (structured log line); skipped if the booking was cancelled meanwhile. |

Workers use `acks_late`; all tasks are idempotent, so at-least-once delivery is safe. Enqueueing is best-effort – a broker outage is logged and **never fails an API request**.

## Edge cases handled

* **Auth:** missing/invalid/expired JWT → 401; admin-only routes → 403; role can't be set at signup; identical error and similar timing for unknown email vs wrong password.
* **Ownership:** other users' bookings/payments return **404**, so IDs can't be probed.
* **Invalid input:** malformed bodies, non-integer IDs, unknown enum values, bad dates → 422; unknown booking → 404; past or >180-day appointments rejected; naive datetimes are treated as UTC.
* **Booking:** a centre must actually offer the test; duplicate slot → 409; the client can't set the amount.
* **Payments:** paying a confirmed or cancelled booking → 409; a second payment while one is `PENDING` → 409; failed payments can be retried; `Idempotency-Key` replays return the original payment (200) and reuse for another booking → 409.
* **Webhooks:** bad/missing signature, invalid payload, unknown payment, duplicates, out-of-order or conflicting events (see above).
* **Race with cancellation:** if a payment succeeds after the booking was cancelled, the payment is recorded, the booking **stays cancelled**, and a `payment_result_not_applied` warning is logged (needs a refund – see improvements).
* **Infrastructure:** Redis down → cache misses and in-memory rate limiting; broker down → requests still succeed.

## Testing

```bash
pytest                                   # 54 tests; Redis/Postgres integration tests auto-skip
REDIS_TEST_URL=redis://localhost:6379/15 \
MIGRATION_TEST_URL=postgresql+psycopg://eve:eve@localhost:5432/eve_migrations \
pytest                                   # all 61
```
API tests run on in-memory SQLite (fast, no services). Redis and migration tests need real services and run in CI
(`.github/workflows/ci.yml`). Coverage by area: auth, catalogue, bookings, payments, webhook idempotency, rate limiting,
Redis caching/invalidation/fallback, Celery retries/expiry/notifications, seed, migrations.

## Design decisions

* **Server-side pricing** – never trust a client-supplied amount; snapshot it on the booking.
* **Idempotency by database constraint**, not by check-then-insert, so it holds under concurrency.
* **Terminal payment states are immutable** – late or contradictory provider events can't rewrite history.
* **404 instead of 403** for other users' resources to avoid leaking which IDs exist.
* **Routers thin, services thick** – state transitions live in one place and are shared by the API and the workers.
* **Redis is an optimisation, not a dependency** – every failure degrades gracefully.
* **Sync SQLAlchemy** (FastAPI runs sync endpoints in a threadpool): simpler transactions/locking for this workload.

## Assumptions

* "Simulated payment" is synchronous by default (≈ 80 % success; `simulate_outcome` overrides it for demos/tests); `PENDING` models a provider that answers later via webhook.
* Slot capacity per centre/time window isn't modelled – only per-user duplicate slots are blocked.
* Only admins manage the catalogue; admins are created by the seed script.
* Currency is INR. Cancelling a confirmed booking doesn't issue a refund.
* Rate limiting keys on the client IP; behind a proxy run uvicorn with `--proxy-headers`.
* A success webhook arriving *after* a stale-payment timeout is ignored (terminal state) and would need manual reconciliation.

## What I'd improve with more time

* Slot capacity and centre opening hours/timezones (exclusion constraint or slot table).
* Refund flow and a payment-events audit table; automatic reconciliation for late success after expiry/cancellation.
* Webhook timestamp tolerance (replay protection) and rotating webhook secrets.
* Refresh tokens / revocation, email verification, password reset.
* Outbox pattern for task enqueueing, dead-letter handling for exhausted webhook retries, and a real notification provider.
* OpenTelemetry tracing + Prometheus metrics; load tests for the payment path.

## Project layout

```
app/
  main.py  config.py  database.py  models.py  schemas.py  security.py  deps.py
  errors.py  cache.py  ratelimit.py  logging_config.py  seed.py
  routers/   auth.py  catalog.py  bookings.py  payments.py      # HTTP layer
  services/  bookings.py  payments.py                           # business rules
  worker/    celery_app.py  tasks.py  dispatch.py               # background jobs
alembic/     env.py  versions/0001_initial_schema.py
tests/       auth · catalog · bookings · payments · webhook · worker · redis · migrations · seed
Dockerfile · docker-compose.yml · Makefile · .github/workflows/ci.yml
```

## Git workflow

History follows [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `test:`, `docs:`, `chore:`), one
logical change per commit. See `git log --oneline`.
