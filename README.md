# ODOS Mobile Backend

FastAPI API for the ODOS marketplace — powers the mobile shopper app, vendor tools, and admin dashboard.

| Repository | GitHub |
|------------|--------|
| Mobile app | [ODOS_MOBILE_CLIENT](https://github.com/ODOS-DEVS/ODOS_MOBILE_CLIENT) |
| Admin dashboard | [ODOS_ADMIN](https://github.com/ODOS-DEVS/ODOS_ADMIN) |

## Stack

- FastAPI · SQLAlchemy 2 · Alembic · Neon Postgres
- JWT auth · Paystack · Cloudinary · Brevo email · Redis (rate limits + catalog cache)
- WebSocket realtime for admin and catalog invalidation

## What the API covers

**Shopper**

- Auth (email/password, Google, verification, password reset, phone OTP)
- Catalog: categories, markets, stores (with business hours + vacation mode), products, deals hub, promo banners, flash sales
- Cart, wishlist, orders, returns, reviews, vouchers, customer wallet, payments
- Delivery: customer-confirmed handoff (vendor dispatches, only the customer can confirm receipt — never the vendor), "I haven't received it" problem reporting, reschedule requests, delivery ratings, SLA monitoring with automatic customer goodwill credit
- 48-hour auto-release safety net (36h reminder) if a customer never confirms, with active problems/reschedules excluded
- Delivery quotes at checkout, configurable delivery settings, order payment SMS
- Push notifications (Expo) with tap-to-navigate payloads
- **Notification read-state** sync and paginated activity feed for the mobile client
- **Recommendations**: `/api/recommendations/for-you`, `/api/recommendations/similar/{product_id}`
- **Behavior tracking**: product views, clicks, search taps (feeds the recommendation engine)
- **In-app AI assistant**: `/api/assistant/chat` and `/api/assistant/chat/stream` with order/cart context when signed in

**Payments**

- Paystack checkout (card/MoMo) and in-app wallet checkout, with server-side recomputation of every order total (the client-submitted amount is never trusted)
- Webhook signature verification, idempotent payment/webhook handling, and a background job that automatically re-verifies payments stuck `pending`
- Row-locked wallet and treasury balance updates — no double-spend / overdraw window on concurrent withdrawal or settlement requests
- Vendor withdrawal requests with Paystack Transfers support, manual payout confirmation, and a visible commission rate
- **Vendor settlement** triggers only on customer-confirmed delivery, auto-release, or an audited admin override with a mandatory reason — a vendor can never self-certify their own payout, and a DB-level constraint blocks double settlement even under concurrent requests

**Promotions**

- Voucher engine: percent/fixed/BOGO/free-shipping, store or platform scope, stacking, priority, auto-apply, and claim-only availability
- Flash sales with real per-item stock caps (auto-reverts to regular price once sold out) and admin nomination review
- Merchandising campaigns with vendor opt-in review, plus admin alert emails for vendor applications, withdrawal requests, and voucher submissions
- Background reminders: shoppers get nudged before a saved voucher expires; vendors get nudged if their own voucher is about to expire unused

**Vendor / Seller Center**

- Store profile (including vacation mode + business hours), products, orders, vouchers, flash sale nominations
- Inventory movements / stock ledger, bulk product updates, reserved vs available stock
- Customers aggregate, reviews list + seller reply, analytics with `7d|30d|90d` periods
- Merchandising campaign opt-ins, wallet / payouts (dual-role approved vendors)
- Vendor notification preference gates (orders, inventory, payouts)
- Dispatch is the vendor's last delivery action — no "mark delivered" exists on this side anymore

**Admin**

- Full CRUD across users, vendors, stores, catalog, orders, finance, notifications
- Review queues for vendor flash-sale nominations and merchandising campaign opt-ins
- **Delivery Ops**: live delivery/settlement status per order, exceptions (customer-reported problems) surfaced first, full delivery event timeline, completion method (customer / auto-release / admin override)
- **Vendor payouts** with Paystack transfer support and manual payout confirmation for Starter accounts
- Feature-scoped admin alert emails (only admins with the relevant permission band are notified) — includes a new alert when a customer reports a delivery problem
- **Paginated admin lists**: `{ items, has_more }` on list endpoints
- Promo banners with `placement`, `link_type`, `campaign_tag`
- Single-record fetch for studio editors (`GET /admin/promo-banners/{id}`)

## Requirements

- Python 3.11+
- A Neon Postgres branch (see below — there is no local database to install)
- Optional but recommended: Redis, Cloudinary, Brevo, Paystack, Arkesel SMS

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the project root (never commit this file). Minimum:

```env
DATABASE_URL=postgresql+psycopg://user:password@ep-xxx.aws.neon.tech/neondb?sslmode=require
SECRET_KEY=replace-with-a-long-random-secret
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### The database

Neon is the database for every environment — local, staging and production. There is no
separate local Postgres to install and no second copy of the data to keep in sync.

For day-to-day work, point `DATABASE_URL` at a Neon **branch** rather than the production
branch. Branches are copy-on-write forks: instant to create, and dropping one cannot touch
production data.

Two things to check on the connection string you paste:

- Keep `?sslmode=require`. Neon refuses plaintext connections.
- Percent-encode the password if it contains `@ : / ? # &`. Anything that parses the URL —
  `pg_dump`, `psql`, the app's own pooling setup — will otherwise read the host wrongly.

Common optional variables:

```env
REDIS_URL=
RATE_LIMIT_ENABLED=true
CACHE_ENABLED=true
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
BREVO_API_KEY=
BREVO_SENDER_EMAIL=
PAYSTACK_SECRET_KEY=
PAYSTACK_PUBLIC_KEY=
PAYSTACK_WEBHOOK_SECRET=
GOOGLE_CLIENT_IDS=
ASSISTANT_ENABLED=true
ASSISTANT_PROVIDER=gemini
ASSISTANT_MODEL=gemini-3.1-flash-lite
GEMINI_API_KEY=
```

Apply migrations and run:

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or bring up the whole stack — API, Celery worker, beat, Redis and nginx — against the same
Neon database:

```bash
docker compose up --build      # API on :8000, through nginx on :8080
```

See [docs/DOCKER.md](docs/DOCKER.md).

- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/api/health

Use `--host 0.0.0.0` when testing from a phone on the same network.

## Migrations

Recent additions (run `alembic upgrade head` after pull):

- User behavior tracking tables (recommendations input)
- Promo banner placement and link metadata
- Promotions / voucher system enhancements
- Seller Center Wave 1: store vacation fields, review reply columns
- Seller Center Wave 2: inventory movements, vendor notification preference columns
- Merchandising campaigns and related catalog flags
- Order status timeline and delivery experience fields (instructions, rating, reschedule, dispatch photo)
- Flash-sale stock caps, flash-attributed order items, and voucher expiry-reminder tracking columns
- Removed the vendor-facing delivery code (superseded by customer-confirmed delivery)
- Delivery/settlement sub-state columns (`delivery_status`, `settlement_status`, `confirmation_method`, auto-release scheduling), richer timeline event metadata, and a DB-level uniqueness guard against double-settling a vendor for the same order
- Loyalty accounts/transactions/tier benefits and `promo_analytics_events`, plus `eligibility_rules` on vouchers and campaigns

`alembic upgrade head` is self-healing. The reconcile revision at head re-checks every object
the promo/loyalty migrations are meant to create and adds whatever is missing, so a database
that was stamped past a migration it never actually ran converges to the right schema instead
of failing at runtime with a missing column.

## Background jobs

The API runs five lightweight polling loops in-process on startup (no separate worker needed):

| Loop | Interval | Purpose |
|------|----------|---------|
| Vendor order reminders | 3 min | Nudges vendors about unfulfilled orders |
| Delivery SLA monitor | 2 min | Flags late deliveries and credits customer goodwill on breach |
| Promo expiry reminders | 30 min | Nudges shoppers/vendors before a voucher expires |
| Payment reconciliation | 5 min | Re-verifies payments/wallet top-ups stuck `pending` directly against Paystack |
| Delivery auto-release | 30 min | Reminds the customer at 36h, then auto-confirms + settles the vendor at 48h if there's no active problem/reschedule |

Set `SCHEDULER_ENABLED=false` to hand the same five jobs to a Celery worker + beat pair
instead — the Docker Compose stack does this. Exactly one of the two must be active: leaving
the in-process loops on while beat is running executes every job twice. It should also be
false on every API replica beyond the first.

## Deployment

Production runs on a VPS behind nginx with TLS:

```text
https://appbe.odos.market/api
```

The `Dockerfile` builds one image used by the API, the Celery worker and beat — they run the
same code and differ only in their command, so there is no chance of the worker running a
different revision than the API.

Migrations are deliberately **not** run on container start: with more than one replica every
container would race to `alembic upgrade head`. Compose runs them once in a dedicated
`migrate` service, and a real deployment runs them as a release step.

Whatever origin serves the admin has to be listed in `CORS_ORIGINS`, or every request fails in
the browser even though the server is healthy.

`render.yaml` is kept for reference. It declares no `databases:` block on purpose — Neon is
the single database, so `DATABASE_URL` is set by hand rather than provisioned.

## API overview

**Public / shopper**

| Area | Prefix |
|------|--------|
| Auth & account | `/api/auth/*`, `/api/account/*` |
| Catalog & deals | `/api/catalog/*`, `/api/deals/*` |
| Recommendations | `/api/recommendations/*` |
| Behavior | `/api/behavior/*` |
| Assistant | `/api/assistant/*` |
| Commerce | `/api/cart*`, `/api/wishlist*`, `/api/orders*` |
| Payments & vouchers | `/api/payments/*`, `/api/vouchers/*` |
| Health | `/api/health` |

Delivery specifics: `PATCH /api/orders/{id}/deliver` (customer confirms), `POST /api/orders/{id}/delivery-problem` (customer reports an issue), `POST /api/orders/{id}/reschedule` (not home).

**Admin**

| Area | Prefix |
|------|--------|
| Auth & dashboard | `/api/admin/auth/*`, `/api/admin/dashboard` |
| Directory lists | `/api/admin/users*`, `/api/admin/vendors*`, `/api/admin/stores*`, … |
| Merchandising | `/api/admin/promo-banners*`, `/api/admin/flash-sale-events*`, `/api/admin/flash-sale-nominations*`, `/api/admin/merchandising-campaign-opt-ins*` |
| Operations | `/api/admin/orders*`, `/api/admin/notifications*`, `/api/admin/finance*`, `/api/admin/delivery-ops*` |

Admin list responses use `{ "items": [...], "has_more": true|false }`.

## Recommendations (how it works)

The recommendation service blends:

- Category and store affinity from user behavior events
- Co-purchase and recency signals
- In-stock and catalog backfill when personalized results are thin

Mobile clients send behavior via `/api/behavior/events` and read feeds from `/api/recommendations/for-you` and `/api/recommendations/similar/{product_id}`.

## Project structure

```text
app/
  controllers/     # Route handlers
  core/            # Auth, cache, pagination, promo config
  models/          # SQLAlchemy models (incl. user_behavior)
  routes/          # FastAPI routers
  schemas/         # Pydantic request/response models
  services/        # recommendation_service, promotion_service, pricing, …
alembic/versions/
```

## Verification

```bash
alembic upgrade head
python3 -m py_compile app/main.py
```

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| Empty recommendations | Behavior migrations applied; user has viewed/clicked products |
| Admin list mismatch | Deploy latest backend so `{ items, has_more }` is returned |
| Promo banner 404 in studio | `GET /admin/promo-banners/{id}` route deployed |
| Redis errors on Render | `REDIS_URL` set; check `/api/health` hint |
| CORS | `CORS_ORIGINS` includes admin and mobile web origins |

## License

Proprietary — ODOS-DEVS.
