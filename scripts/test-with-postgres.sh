#!/usr/bin/env bash
# Run the integration suite against a throwaway Postgres.
#
# The guarantees these tests cover — partial unique indexes, SELECT ... FOR
# UPDATE — do not exist in SQLite, so they are skipped unless a real database
# is configured. This script provides one.
set -euo pipefail

DOCKER="${DOCKER_BIN:-docker}"
command -v "$DOCKER" >/dev/null 2>&1 || DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
export PATH="$(dirname "$DOCKER"):$PATH"

CONTAINER="${TEST_PG_CONTAINER:-odos-test-pg}"
PORT="${TEST_PG_PORT:-55432}"
URL="postgresql+psycopg://odos_user:odos_local_password@localhost:${PORT}/odos_mobile"

if ! "$DOCKER" ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> starting $CONTAINER on :$PORT"
  "$DOCKER" rm -f "$CONTAINER" >/dev/null 2>&1 || true
  "$DOCKER" run -d --name "$CONTAINER" \
    -e POSTGRES_USER=odos_user \
    -e POSTGRES_PASSWORD=odos_local_password \
    -e POSTGRES_DB=odos_mobile \
    -p "${PORT}:5432" postgres:18-alpine >/dev/null
  for _ in $(seq 1 45); do
    "$DOCKER" exec "$CONTAINER" pg_isready -U odos_user -d odos_mobile >/dev/null 2>&1 && break
    sleep 1
  done
fi

echo "==> migrating to head"
DATABASE_URL="$URL" .venv/bin/python -m alembic upgrade head >/dev/null

echo "==> running tests"
TEST_DATABASE_URL="$URL" .venv/bin/python -m pytest "${@:-tests}" -q

echo
echo "Database left running as '$CONTAINER'. Remove it with:"
echo "  $DOCKER rm -f $CONTAINER"
