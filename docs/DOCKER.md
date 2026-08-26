# Running ODOS in Docker

One command brings up the whole backend — Postgres, Redis, migrations, the API, a Celery worker,
Celery beat and nginx:

```bash
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:8080 | API **through nginx** — what production looks like |
| http://localhost:8000 | API **direct** — bypasses the proxy, for debugging |
| http://localhost:8080/docs | Swagger UI |
| http://localhost:8080/api/health | Liveness |
| localhost:5433 | Postgres (5433 on the host, since 5432 is usually taken) |
| localhost:6379 | Redis |

Stop with `docker compose down`; add `-v` to also wipe the database volume.

---

## What runs, and why

| Service | Role | Notes |
|---|---|---|
| `postgres` | Database | Container **for local only** — production should use a managed one |
| `redis` | Rate limits, catalog cache, Celery queue | No persistence: it holds no business truth |
| `migrate` | `alembic upgrade head` | Runs once and exits; everything else waits for it |
| `api` | FastAPI under uvicorn | `--reload`, bind-mounted against your working tree |
| `worker` | Celery worker | Executes the periodic jobs |
| `beat` | Celery scheduler | Decides *when*. **Exactly one may run** |
| `nginx` | Reverse proxy | TLS termination, buffering, compression, size caps |

The API, worker and beat share **one image** and differ only in their command. Three images
would be three things to keep in sync and three chances for the worker to run a different
revision than the API.

---

## Background jobs: two modes, never both

The five periodic jobs — vendor reminders, delivery SLA, promo expiry, payment reconciliation,
delivery auto-release — can run either way:

**In-process (the default, unchanged).** `SCHEDULER_ENABLED=true` starts them as asyncio loops
inside the API process, exactly as before. Correct for a single API process, and what the
current Render deployment does.

**Celery.** `SCHEDULER_ENABLED=false` on the API, with `worker` and `beat` running. Compose does
this.

> **Leaving both on runs every job twice** — duplicate vendor pushes, duplicate SLA alerts, and
> two concurrent payment reconciliation passes over the same pending payments. `SCHEDULER_ENABLED`
> exists precisely to make that impossible to do by accident.

The same applies when scaling the API: set `SCHEDULER_ENABLED=false` on every replica beyond the
first, or each one runs its own copy of every job.

### Why Celery is worth it here

Only once you run more than one API container. At that point the in-process loops multiply by
replica count, and the most consequential of them —
`process_stuck_payment_reconciliation`, which rescues orders whose webhook never arrived —
starts running concurrently against the same rows.

The tasks in `app/tasks.py` are deliberately thin wrappers around the existing service
functions, so both modes run identical code and cannot drift.

### Operating it

```bash
docker compose logs -f worker beat            # watch the schedule
docker compose exec worker celery -A app.core.celery_app.celery_app inspect active
docker compose exec worker celery -A app.core.celery_app.celery_app inspect registered

# Run one job immediately, without waiting for its interval
docker compose exec worker python -c \
  "from app.tasks import payment_reconciliation; print(payment_reconciliation.delay().get(timeout=60))"
```

`task_acks_late=True` means a task is acknowledged only after it finishes — a worker killed
mid-task gets its message redelivered rather than losing it. The cost is that a task can run
twice, which is safe here because all five re-read current state and act only on rows still
needing action.

---

## nginx

One detail is worth knowing about, because it is a security control and not just plumbing:

```nginx
proxy_set_header X-Forwarded-For $remote_addr;      # overwrite
```

not

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # append (the common default)
```

The application identifies a caller by the **leftmost** `X-Forwarded-For` entry. With the append
form, nginx keeps whatever the client sent and adds the real address after it — so a client can
send `X-Forwarded-For: 1.2.3.4` and be identified as `1.2.3.4`, defeating the per-IP rate limits
on login, signup, password reset and OTP by rotating one header.

Overwriting discards the client's claim. nginx is the only hop, so `$remote_addr` is the real peer.

> **If you put a CDN or load balancer in front of this nginx**, `$remote_addr` becomes that
> device and every request will be attributed to it. That line has to change with the topology.

nginx also serves `/uploads/` directly rather than proxying, so uvicorn workers are not tied up
streaming files, and maps `Connection: upgrade` so the `/api/ws` realtime channel works.

---

## Configuration

Compose sets everything the stack needs inline, so it starts with no `.env` at all. `.env` is
**excluded from the image** (`.dockerignore`) — secrets must never be baked into a layer.

To use real integrations locally (Paystack, Cloudinary, Brevo, Arkesel, Gemini), add them to the
`x-app-env` block in `docker-compose.yml`, or point the services at an env file:

```yaml
    env_file:
      - .env.docker
```

Copy `.env.example` as a starting point. Note `SECRET_KEY` in Compose is a fixed development
value — replace it anywhere that matters.

---

## Migrations

`migrate` runs `alembic upgrade head` once and exits; `api`, `worker` and `beat` all wait for it
to succeed. Nothing ever queries an unmigrated schema.

The Dockerfile deliberately does **not** run migrations on container start: with more than one
API replica every container would race to `upgrade head` simultaneously. In a real deployment,
run them as a release step.

```bash
docker compose run --rm migrate                                     # apply
docker compose run --rm --entrypoint "" migrate python -m alembic current
docker compose run --rm --entrypoint "" migrate \
  python -m alembic revision --autogenerate -m "description"
```

---

## Production notes

What here maps to production, and what does not:

* **`postgres` and `redis` are development conveniences.** Use managed services — backups,
  failover and patching are the entire reason to pay for a database.
* **`api`, `worker`, `beat` and `nginx` are real.** Deploy the same image with the same three
  commands.
* **nginx may be redundant.** Render, ALB, Cloud Run and similar already terminate TLS and load
  balance. Adding nginx behind one of those duplicates their job and adds a hop to misconfigure.
  It earns its place on a plain VM.
* **Remove `--reload` and the `./app` bind mount.** Both are development-only; the image already
  contains the code.
* Set a real `SECRET_KEY`, real `CORS_ORIGINS`, and the Paystack/Cloudinary/Brevo credentials.

---

## Troubleshooting

**`api` exits immediately.** Almost always a database it cannot reach. `docker compose logs api`;
check `migrate` exited 0.

**Jobs running twice.** `SCHEDULER_ENABLED` is true somewhere while beat is also running.
`docker compose exec api env | grep SCHEDULER`.

**`worker` starts but nothing executes.** beat is the scheduler — check it is up
(`docker compose ps beat`) and that both point at the same `REDIS_URL`.

**nginx 502.** The API is down or still starting. `docker compose ps` — `api` should be healthy;
hit http://localhost:8000/api/health directly to isolate proxy from app.

**Port already in use.** Change the host side of the mapping (`"8081:80"`). Postgres is already
on 5433 for this reason.
