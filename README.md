# ODOS Mobile Backend

FastAPI API for the ODOS marketplace — powers the mobile shopper app, vendor tools, and admin dashboard.

| Repository | GitHub |
|------------|--------|
| Mobile app | [ODOS_MOBILE_CLIENT](https://github.com/ODOS-DEVS/ODOS_MOBILE_CLIENT) |
| Admin dashboard | [ODOS_ADMIN](https://github.com/ODOS-DEVS/ODOS_ADMIN) |

## Stack

- FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL
- JWT auth · Paystack · Cloudinary · Brevo email · Redis (rate limits + catalog cache)
- WebSocket realtime for admin and catalog invalidation

## What the API covers

**Shopper**

- Auth (email/password, Google, verification, password reset, phone OTP)
- Catalog: categories, markets, stores, products, deals hub, promo banners, flash sales
- Cart, wishlist, orders, returns, reviews, vouchers, customer wallet, payments
- **Recommendations**: `/api/recommendations/for-you`, `/api/recommendations/similar/{product_id}`
- **Behavior tracking**: product views, clicks, search taps (feeds the recommendation engine)

**Vendor**

- Store profile, products, orders, vouchers, flash sale nominations

**Admin**

- Full CRUD across users, vendors, stores, catalog, orders, finance, notifications
- **Cursor-style admin pagination**: `{ items, has_more }` on list endpoints
- Promo banners with `placement`, `link_type`, `campaign_tag`
- Single-record fetch for studio editors (`GET /admin/promo-banners/{id}`)

## Requirements

- Python 3.11+
- PostgreSQL
- Optional but recommended: Redis (Upstash or Render), Cloudinary, Brevo, Paystack, Arkesel SMS

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the project root (never commit this file). Minimum:

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/odos_mobile
SECRET_KEY=replace-with-a-long-random-secret
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

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
```

Apply migrations and run:

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/api/health

Use `--host 0.0.0.0` when testing from a phone on the same network.

## Migrations

Recent additions (run `alembic upgrade head` after pull):

- User behavior tracking tables (recommendations input)
- Promo banner placement and link metadata
- Promotions / voucher system enhancements

## Deployment (Render)

This repo includes `render.yaml` for a Blueprint with web service + Postgres.

1. Connect the GitHub repo in Render and deploy the Blueprint.
2. Set secrets: `CORS_ORIGINS`, Cloudinary, Brevo, Paystack, Google client IDs, Redis URL.
3. `DATABASE_URL` and `SECRET_KEY` are provisioned by Render.
4. Migrations run on startup via `alembic upgrade head`.

Production API base:

```text
https://odos-backend.onrender.com/api
```

Add admin and any web client origins to `CORS_ORIGINS`.

## API overview

**Public / shopper**

| Area | Prefix |
|------|--------|
| Auth & account | `/api/auth/*`, `/api/account/*` |
| Catalog & deals | `/api/catalog/*`, `/api/deals/*` |
| Recommendations | `/api/recommendations/*` |
| Behavior | `/api/behavior/*` |
| Commerce | `/api/cart*`, `/api/wishlist*`, `/api/orders*` |
| Payments & vouchers | `/api/payments/*`, `/api/vouchers/*` |
| Health | `/api/health` |

**Admin**

| Area | Prefix |
|------|--------|
| Auth & dashboard | `/api/admin/auth/*`, `/api/admin/dashboard` |
| Directory lists | `/api/admin/users*`, `/api/admin/vendors*`, `/api/admin/stores*`, … |
| Merchandising | `/api/admin/promo-banners*`, `/api/admin/flash-sale-events*` |
| Operations | `/api/admin/orders*`, `/api/admin/notifications*`, `/api/admin/finance*` |

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
