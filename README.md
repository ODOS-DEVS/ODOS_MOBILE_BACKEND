# ODOS Backend

The API behind ODOS — a multi-vendor marketplace. One shopper's cart can hold
products from several vendors; each vendor fulfils and delivers their own part
of the order. Most of the interesting logic in here follows from that.

Serves the mobile app, the vendor tools inside it, and the admin dashboard.

| | |
|---|---|
| Mobile app | [ODOS_MOBILE_CLIENT](https://github.com/ODOS-DEVS/ODOS_MOBILE_CLIENT) |
| Admin dashboard | [ODOS_ADMIN](https://github.com/ODOS-DEVS/ODOS_ADMIN) |

## What it's built on

FastAPI, SQLAlchemy 2, Alembic, PostgreSQL. Redis for rate limiting and
catalogue caching, Celery for background and scheduled work, WebSockets for
live admin and catalogue updates. Paystack for payments, Cloudinary for media,
Brevo for email, Arkesel for SMS. Deployed with Coolify on a self-hosted VPS.

## The API reference is generated, not written

FastAPI publishes the whole surface from the code itself, so there is no
hand-maintained endpoint list here to fall out of date:

| | |
|---|---|
| Swagger UI | `/docs` |
| OpenAPI JSON | `/openapi.json` |
| Health | `/api/health` |

That is ~310 operations across ~275 schemas. If you are building a client,
generate it rather than hand-writing calls:

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o schema.d.ts
```

One thing the spec gets wrong, because the handler reads the raw request
instead of declaring a typed body: **`POST /api/auth/login` accepts either**
`{"email", "password"}` as JSON, or form-encoded `username`/`password` in the
OAuth2 style. The spec shows no body at all.

## Running it locally

Python 3.13 and Docker.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in the project root — it is gitignored and must stay that way.
The minimum that will boot:

```env
DATABASE_URL=postgresql+psycopg://odos:odos@localhost:5432/odos_mobile
SECRET_KEY=replace-with-a-long-random-secret
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Bring up Postgres, apply migrations, run the API:

```bash
docker compose up -d postgres
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Use `--host 0.0.0.0` if you are testing from a phone on the same network.

Or run the whole stack — Postgres, Redis, migrations, API, Celery worker, beat
and nginx — at once:

```bash
docker compose up --build     # API on :8000, through nginx on :8080
```

Everything else is optional and degrades gracefully when unset: `REDIS_URL`,
the `CLOUDINARY_*` keys, `BREVO_*`, `PAYSTACK_*`, `GOOGLE_CLIENT_IDS`, and the
`ASSISTANT_*` / `GEMINI_API_KEY` group. `GET /api/health/services` tells you
which of them are actually live.

### Two things that will bite you on a connection string

**TLS.** A managed provider requires it; a plain container cannot offer it. The
app decides which applies from the shape of the host — a single-label name
(`db`, `postgres`, any container name) is treated as internal, a
fully-qualified one (`*.neon.tech`, `*.rds.amazonaws.com`) as public. An
explicit `?sslmode=` in the URL always wins.

**Passwords containing `@ : / ? # &`** must be percent-encoded. Anything that
parses the URL — `pg_dump`, `psql`, the app's own pooling setup — will
otherwise read the host wrongly and fail somewhere confusing.

## Checks

```bash
pytest          # 244 tests
ruff check .    # clean; config lives in pyproject.toml
```

Both should pass before anything is pushed. Two ruff rules are switched off on
purpose, and the reasons are written down in `pyproject.toml` rather than left
for someone to rediscover: `B008` conflicts with FastAPI's `Depends()` idiom,
and `UP042` would rewrite `(str, Enum)` as `StrEnum`, which is *not*
equivalent — they render differently in f-strings, and these enums reach API
responses and customer-facing copy.

## Layout

```text
app/
  routes/        FastAPI routers — endpoint definitions and dependencies
  controllers/   request-level logic; what a route actually does
  services/      domain logic, reusable across controllers
  models/        SQLAlchemy tables
  schemas/       Pydantic request/response shapes
  core/          auth, database, config, caching, pagination
  helpers/       small shared utilities, incl. the admin audit log
alembic/versions/  migrations
tests/
```

A route should stay thin: validate, delegate, return. If logic is worth
testing, it belongs in a service.

### Orders split into packages

The part most likely to catch you out. One `Order` is what the shopper pays
for. It is split into one `OrderPackage` per vendor, and the package — not the
order — is the unit of fulfilment, delivery and settlement. There is a unique
constraint on `(order_id, vendor_user_id)`.

An order's overall status is the **least advanced** of its packages, so a
problem on one vendor's package surfaces above another's "delivered" rather
than being hidden by it.

Two rules worth knowing before touching payouts:

- A vendor is never paid on their own say-so. Settlement fires on
  customer-confirmed delivery, on auto-release, or on an audited admin
  override with a mandatory reason. A database constraint blocks double
  settlement even under concurrent requests.
- Order totals are always recomputed server-side. A client-submitted amount is
  never trusted.

## Migrations

```bash
alembic upgrade head                        # apply
alembic revision --autogenerate -m "..."    # create, then read what it wrote
```

Always read a generated migration before committing it. Autogenerate is good
at columns and bad at intent — it will happily produce a drop-and-recreate for
something that wanted an `ALTER`.

**Deploys do not migrate on their own.** The Dockerfile deliberately does not
run alembic, and Coolify builds from the Dockerfile rather than the compose
file, so the `migrate` service in `docker-compose.yml` never runs in
production. Run `alembic upgrade head` against production yourself after
deploying anything with a schema change. Skipping this has caused an outage
before — the API booted fine and then 500'd on the first query touching a
missing column.

## Deployment

Coolify builds the Dockerfile and runs the API, worker and beat. Configuration
is environment variables only; there is nothing to edit on the server.

Worth knowing:

- Changing an environment variable needs a **Redeploy**, not a Restart. A
  "Changes pending" badge means the running container is still on the old
  values.
- `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` bound how many database connections the
  API can hold. Set them too low and requests queue behind the pool and time
  out under load, which looks like a slow database rather than a small pool.

## Troubleshooting

| What you see | Where to look |
|---|---|
| `QueuePool limit ... reached` | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`; redeploy, don't restart |
| 500s right after a deploy | migrations not applied — `alembic upgrade head` |
| Browser calls blocked | `CORS_ORIGINS` must list the exact origin, scheme included |
| Rate limiting or caching silently off | `REDIS_URL` unset — confirm at `/api/health/services` |
| Empty recommendations | behaviour tables need data; the user must have viewed products |

## Licence

Proprietary — ODOS-DEVS.
