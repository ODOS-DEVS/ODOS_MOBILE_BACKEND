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
pytest          # 237 pass; 47 more need Postgres (see tests/conftest.py)
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

**Deploys run migrations via a Coolify pre-deploy hook**, not from the image.
The Dockerfile deliberately does not run alembic — with more than one replica
every container would race to `alembic upgrade head` on boot. Instead Coolify
executes it once, before the new build starts, under Deployment lifecycle. You
can see it at the top of any deployment log:

```
[CMD]: docker exec … sh -c 'alembic upgrade head'
```

So a schema change ships with its deploy. If that hook is ever removed, it
goes back to being manual — and skipping it has caused an outage before, where
the API booted fine and then 500'd on the first query touching a missing
column.

## Deployment

Coolify builds the Dockerfile and runs the API, worker and beat. Configuration
is environment variables only; there is nothing to edit on the server.

The build strategy must stay on **Dockerfile**. It was briefly set to Railpack,
which ignores this repo's Dockerfile and builds its own image via mise. That
broke on 1 October 2026 when mise could no longer fetch a precompiled Python
3.13.16 — a failure with no connection to anything in this repository, and one
that only appeared once the build cache for that step expired.

Worth knowing:

- Changing an environment variable needs a **Redeploy**, not a Restart. A
  "Changes pending" badge means the running container is still on the old
  values.
- `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` bound how many database connections the
  API can hold. Set them too low and requests queue behind the pool and time
  out under load, which looks like a slow database rather than a small pool.

## Payments

KutanaPay collects order payments. Paystack is still live for two things it was
never replaced for: customer wallet top-ups and every vendor payout. So
"switching payments to live" is two switches, not one, and the second is easy
to forget because nothing about an order checkout touches it.

| What | Gateway | Env |
|---|---|---|
| Order checkout | KutanaPay | `KUTANAPAY_API_KEY`, `KUTANAPAY_WEBHOOK_SECRET` |
| Wallet top-up | Paystack | `PAYSTACK_SECRET_KEY`, `PAYSTACK_WEBHOOK_SECRET` — refuses unless the key is `sk_live_` |
| Vendor payout | Paystack | `PAYSTACK_SECRET_KEY`, `PAYSTACK_PAYOUTS_ENABLED` |

### Going live

`GET /api/health/payments` reports what the running container is actually
configured with, so each step below can be confirmed before the next. It
returns no secret -- credentials appear as the first 8 hex of their SHA-256, so
"is this the live key or still the test one" is answerable by comparison:

```
printf %s "$KUTANAPAY_API_KEY" | shasum -a 256 | cut -c1-8
```

1. **Get the live credentials *and* the production API host.** Both, together.
   `api.kutanapay.com` is the **sandbox** API: a key issued in the live portal
   is rejected there with `401 {"detail":"Invalid API key"}`, while a sandbox
   key succeeds and returns `checkout_url` values on `dev.kutanapay.com`.

   A sandbox checkout looks completely healthy and never pays. It reaches the
   hosted page, accepts a phone number, and then sits at "Waiting" until it
   expires -- no prompt, no error, nothing in any log. If payments are not
   arriving, check the host before anything else.

   Secret keys carry no `test`/`live` marker (both are `mk_…`), so the key
   itself tells you nothing. The publishable key does (`pk_live_…`), and the
   `checkout_url` host is the other giveaway.

   KutanaPay publish no developer docs -- no `docs.`, `developer.` or
   `developers.` host resolves -- so the production base URL has to come from
   them directly.
2. **Register the webhook** in the KutanaPay dashboard, on the live account:

   ```
   https://appbe.odos.market/api/payments/kutanapay/webhook
   ```

   Test and live are separate registrations. A live key with the webhook only
   registered on sandbox is the worst of the failure modes: checkouts succeed,
   customers are charged, and no order is ever activated.
3. **Set `KUTANAPAY_WEBHOOK_SECRET` to the live signing secret.** It is a
   different secret per mode. Note that `kutanapay_is_configured` does not
   require it -- checkouts are created without it and only the callback fails,
   so a wrong value here looks like working checkout and silently broken
   fulfilment. `/api/health/payments` reports it as `ready_to_confirm`.
4. **Decide about Paystack.** Leaving it on test keys is a supported choice,
   but it has one hard consequence: **wallet top-ups refuse with a 503 while
   Paystack is not live.** They have to. A top-up credits `available_balance`
   directly, that balance buys real goods through `create_wallet_checkout`, and
   Paystack publishes its test card numbers — so a test-mode top-up is free
   spendable money and a vendor owed a real settlement against none. The gate
   fails closed: a key that is not positively `sk_live_` is treated as not
   live. `WALLET_TOPUPS_ALLOW_TEST_GATEWAY=true` overrides it, and is only ever
   correct on staging.

   Vendor payouts are not gated — they are admin-initiated, not customer-facing
   — but on a test key they do not move real funds. `paystack.mode` and
   `wallet_topups_allowed` on the health endpoint report both.
5. **Redeploy, don't restart.** An env change needs a Redeploy to reach the
   container; see Deployment above.
6. **Confirm, then test with a real payment** -- small, on a real card or
   wallet, end to end, and check the order activates. The reconciliation loop
   only steps in after `GRACE_WINDOW_MINUTES`, so an order that activates
   immediately proves the webhook, which is the part that silently breaks.

The app needs no new build for any of this. It opens whatever
`authorization_url` the backend hands it, so the gateway and its mode are
entirely a backend concern. `EXPO_PUBLIC_COLLECTIONS_PROVIDER` only chooses
*which* gateway, and exists so a live collection problem can be rolled back to
Paystack without shipping through review.

### If a payment is taken and the order stays pending

That is a webhook that did not arrive or did not verify. Nothing is lost -- a
background loop re-verifies pending transactions against their own gateway
every 5 minutes, from 10 minutes old until 24 hours old, and the app re-verifies
whenever the customer returns to it. Check, in order: the webhook registration
on the **live** account, `ready_to_confirm` on the health endpoint, and the logs
for `Failed reconciling stuck … order payment`.

Each transaction is verified against the provider stored on its own row. This
is load-bearing: references are provider-specific, and asking the wrong gateway
returns "not found", which reads as a failure. Rows from the retired iPay
integration are still in the table and are deliberately left untouched for
exactly that reason.

## Troubleshooting

| What you see | Where to look |
|---|---|
| `QueuePool limit ... reached` | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`; redeploy, don't restart |
| 500s right after a deploy | migrations not applied — `alembic upgrade head` |
| Browser calls blocked | `CORS_ORIGINS` must list the exact origin, scheme included |
| Rate limiting or caching silently off | `REDIS_URL` unset — confirm at `/api/health/services` |
| Empty recommendations | behaviour tables need data; the user must have viewed products |
| Paid but order still pending | webhook not registered on the **live** KutanaPay account — `/api/health/payments` |
| Checkout 503s, "temporarily unavailable" | `KUTANAPAY_API_KEY` unset — `ready_to_collect` on the same endpoint |
| Test money taken in production | key still on sandbox; compare `api_key_fingerprint` against the live key |
| Checkout opens but no MoMo prompt, expires at "Waiting" | `KUTANAPAY_BASE_URL` is the sandbox API — see Payments |
| `401 {"detail":"Invalid API key"}` from the gateway | live key against the sandbox host, or the reverse |
| Wallet top-up 503s | by design while `PAYSTACK_SECRET_KEY` is not `sk_live_` — see Payments |

## Licence

Proprietary — ODOS-DEVS.
