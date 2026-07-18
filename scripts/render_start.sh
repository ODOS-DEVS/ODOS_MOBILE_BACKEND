#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ODOS backend startup"
echo "==> Python: $(python --version 2>&1)"
echo "==> Alembic head files:"
ls -1 alembic/versions/*.py | tail -5

echo "==> Recovering alembic version if deploy artifact is behind production DB"
python scripts/alembic_recover.py || true

echo "==> Alembic current revision (pre-upgrade):"
python -m alembic current || true

echo "==> Running migrations"
python -m alembic upgrade head

echo "==> Alembic current revision (post-upgrade):"
python -m alembic current || true

echo "==> Starting API"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
