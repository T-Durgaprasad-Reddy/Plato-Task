# Playto Payout Engine

A production-grade payout engine built for the Playto Founding Engineer Challenge. Implements ledger-based accounting, database-level concurrency safety, idempotency, and resilient background processing.

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Django 4.1 + Django REST Framework |
| Database | PostgreSQL (SQLite for local dev) |
| Queue/Workers | Celery + Redis |
| Beat Scheduler | Celery Beat (retry stuck payouts every 15s) |
| Frontend | React + Vite + Tailwind CSS v4 |

## Core Design Principles

1. **Append-only ledger** — balance is never stored, always derived via `SUM(CREDIT) - SUM(DEBIT) - SUM(HOLD) + SUM(RELEASE)` using `BigIntegerField` in paise
2. **SELECT FOR UPDATE** — row-level DB lock on merchant row prevents double-spend race conditions
3. **DB-backed idempotency** — `unique_together(merchant, idempotency_key)` on Payout; `IntegrityError` catch handles the in-flight race window
4. **State machine in `save()`** — illegal transitions (e.g., `failed → completed`) raise `ValidationError` at the model level
5. **Celery Beat** — `retry_stuck_payouts_task` runs every 15s to recover hung payout workers

---

## Quick Start (Local Dev)

### Backend

```bash
# 1. Activate virtualenv
.\venv\Scripts\activate          # Windows
source venv/bin/activate         # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Migrate and seed
python manage.py migrate
python manage.py seed_data       # Creates 3 merchants + bank accounts + credits

# 4. Run server
python manage.py runserver       # http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

The Vite dev server proxies `/api` → `http://localhost:8000` automatically.

### Run Tests

```bash
python manage.py test payouts.tests -v2
```

**11 tests cover:**
- Concurrency (ThreadPoolExecutor, 2 parallel requests, 1 HOLD asserted)
- Idempotency (same key → same payout ID, 1 DB row)
- State machine (all illegal transitions blocked)
- Ledger integrity (all 4 entry types, balance formula correctness)

---

## Docker (Full Stack)

```bash
docker-compose up --build
```

Services started:
- `db` — PostgreSQL 15
- `redis` — Redis 7
- `backend` — Django (auto-migrates + seeds on start)
- `worker` — Celery worker (4 concurrent)
- `beat` — Celery Beat (retry_stuck_payouts every 15s)
- `frontend` — Vite dev server

Access: Backend `http://localhost:8000` | Frontend `http://localhost:5173`

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/merchants/` | List all merchants with balances |
| GET | `/api/v1/merchants/<id>/balance/` | Available + held balance for merchant |
| GET | `/api/v1/merchants/<id>/bank-accounts/` | Bank accounts for merchant |
| GET | `/api/v1/merchants/<id>/ledger/` | Last 20 ledger entries |
| GET | `/api/v1/payouts/?merchant_id=<id>` | List payouts |
| POST | `/api/v1/payouts/` | Create payout (requires `Idempotency-Key` header) |

### POST /api/v1/payouts/

```
Headers:
  Idempotency-Key: <uuid4>   (required)

Body:
  {
    "merchant_id": 1,
    "bank_account_id": 2,
    "amount_paise": 5000
  }

Responses:
  201 Created  — new payout created
  200 OK       — replay (same idempotency key, returns original payout)
  400 Bad Request — insufficient balance / validation error
```

---

## Railway Deployment

1. Create a Railway project
2. Add **PostgreSQL** plugin → copy `DATABASE_URL`
3. Add **Redis** plugin → copy `REDIS_URL`
4. Set environment variables on the backend service:
   ```
   DATABASE_URL=<from postgres plugin>
   CELERY_BROKER_URL=<from redis plugin>
   CELERY_RESULT_BACKEND=<from redis plugin>
   CELERY_TASK_ALWAYS_EAGER=False
   DEBUG=False
   DJANGO_SECRET_KEY=<generate a strong key>
   ```
5. Set start command: `python manage.py migrate && python manage.py seed_data && gunicorn config.wsgi`
6. Add a second service from the same repo for the Celery worker: `celery -A config worker --loglevel=info`
7. Add a third service for Celery Beat: `celery -A config beat --loglevel=info`

---

## Project Structure

```
playto-task/
├── config/
│   ├── settings.py       # PostgreSQL + Celery Beat config
│   ├── celery.py         # Celery app init
│   └── urls.py
├── payouts/
│   ├── models.py         # Merchant, BankAccount, LedgerEntry, Payout
│   ├── services.py       # PayoutService (create + transition)
│   ├── tasks.py          # process_payout_task, retry_stuck_payouts_task
│   ├── views.py          # All API views
│   ├── serializers.py
│   ├── urls.py
│   ├── tests.py          # 11 tests
│   └── management/
│       └── commands/
│           └── seed_data.py
├── frontend/
│   └── src/App.jsx       # React dashboard
├── docker-compose.yml    # db + redis + backend + worker + beat + frontend
├── Dockerfile
└── EXPLAINER.md
```
