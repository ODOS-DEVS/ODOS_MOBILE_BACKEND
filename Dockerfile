# syntax=docker/dockerfile:1

# One image, three roles. The API, the Celery worker and Celery beat all run the same code and
# differ only in their command — so they share an image. Building three would mean three things
# to keep in sync and three chances for the worker to run a different revision than the API.

# ---------- builder ----------
# Wheels are compiled here with a full toolchain, then only the resulting venv is copied into
# the runtime. Keeps compilers out of the shipped image: smaller, and a smaller attack surface.
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# libpq-dev for psycopg; build-essential for anything without a matching manylinux wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copied alone so this layer is cached until the dependency list actually changes — editing
# application code then rebuilds in seconds rather than re-resolving every wheel.
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


# ---------- runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq5 is the runtime half of libpq-dev. curl backs the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged. A process that cannot write to its own code directory cannot be made to persist
# a compromise there — and Celery refuses to run as root anyway, for the same reason.
RUN useradd --create-home --uid 10001 odos

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=odos:odos alembic.ini ./
COPY --chown=odos:odos alembic ./alembic
COPY --chown=odos:odos scripts ./scripts
COPY --chown=odos:odos app ./app

# Local-disk media is a development fallback only — a container filesystem does not survive a
# redeploy, so production serves media from Cloudinary.
RUN mkdir -p /app/uploads && chown -R odos:odos /app/uploads

USER odos
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Migrations are deliberately NOT run here. With more than one API replica every container
# would race to `alembic upgrade head` on boot; Compose runs them once in a dedicated `migrate`
# service, and a real deployment runs them as a release step.
#
# --proxy-headers so uvicorn honours X-Forwarded-Proto from nginx and generates https:// URLs.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
