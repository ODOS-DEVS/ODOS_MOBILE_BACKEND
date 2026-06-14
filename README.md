# ODOS Mobile Backend

FastAPI API for the ODOS marketplace: mobile shopper app, vendor flows, and admin dashboard.

| Repository | GitHub |
|------------|--------|
| Mobile app | [ODOS_MOBILE_CLIENT](https://github.com/ODOS-DEVS/ODOS_MOBILE_CLIENT) |
| Admin dashboard | [ODOS_ADMIN](https://github.com/ODOS-DEVS/ODOS_ADMIN) |

## Stack

- FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL
- JWT auth · Paystack · Cloudinary · Brevo email · Redis (rate limits + catalog cache)

## Features

- Auth: email/password, Google, email verification, password reset, phone OTP
- Catalog: categories, markets, stores, products, promo banners, flash sale events
- Commerce: cart, wishlist, orders, returns, reviews, vouchers, customer wallet
- Admin: users, vendors, stores, products, orders, finance, notifications
- Real-time catalog cache invalidation and Redis-backed rate limiting

## Requirements

- Python 3.11+
- PostgreSQL
- Optional: Redis (Upstash or Render), Cloudinary, Brevo, Paystack, Arkesel SMS

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the project root (never commit this file). Minimum variables:

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

Use `--host 0.0.0.0` when testing from a physical device on the same network.

## Deployment (Render)

This repo includes `render.yaml` for a Blueprint with web service + Postgres.

1. Connect the GitHub repo in Render and deploy the Blueprint.
2. Set secrets in Render: `CORS_ORIGINS`, Cloudinary, Brevo, Paystack, Google client IDs.
3. `DATABASE_URL` and `SECRET_KEY` are provisioned by Render.
4. Migrations run on startup via `alembic upgrade head`.

Production API base URL:

```text
https://odos-backend.onrender.com/api
```

Add your admin and any web client origins to `CORS_ORIGINS`.

## API overview

**Public / shopper**

- `/api/auth/*` · `/api/account/*` · `/api/cart*` · `/api/wishlist*`
- `/api/catalog/*` · `/api/orders*` · `/api/notifications*`
- `/api/vouchers/*` · `/api/payments/*` · `/api/health`

**Admin**

- `/api/admin/auth/*` · `/api/admin/dashboard`
- `/api/admin/users*` · `/api/admin/vendors*` · `/api/admin/stores*`
- `/api/admin/categories*` · `/api/admin/products*`
- `/api/admin/promo-banners*` · `/api/admin/flash-sale-events*`
- `/api/admin/orders*` · `/api/admin/notifications*`

## Project structure

```text
app/
  controllers/
  core/
  models/
  routes/
  schemas/
  services/
alembic/versions/
```

## Verification

```bash
alembic upgrade head
python3 -m py_compile app/main.py
```

## License

Proprietary — ODOS-DEVS.
