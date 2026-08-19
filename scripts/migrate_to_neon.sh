#!/usr/bin/env bash
# One-shot Postgres migration: dumps SOURCE_DATABASE_URL and restores it into
# TARGET_DATABASE_URL, then verifies every table's row count matches on both
# sides. Never drops or modifies the source database — it's read-only here.
#
# Runs pg_dump/pg_restore/psql via the official postgres:18 Docker image
# rather than local binaries, so the client always matches (or exceeds) the
# source server's version regardless of what's installed on the host.
#
# Usage:
#   export SOURCE_DATABASE_URL="postgresql://...render..."
#   export TARGET_DATABASE_URL="postgresql://...neon..."
#   bash scripts/migrate_to_neon.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -z "${SOURCE_DATABASE_URL:-}" || -z "${TARGET_DATABASE_URL:-}" ]]; then
  echo "Set SOURCE_DATABASE_URL (Render) and TARGET_DATABASE_URL (Neon) first." >&2
  exit 1
fi

PG_IMAGE="postgres:18"

run_pg() {
  docker run --rm -v "$(pwd):/workspace" -w /workspace "${PG_IMAGE}" "$@"
}

DUMP_FILE="odos_migration_$(date +%Y%m%d_%H%M%S).dump"

echo "==> Pulling ${PG_IMAGE} (skips if already cached)"
docker pull -q "${PG_IMAGE}" >/dev/null

echo "==> Dumping source database to ${DUMP_FILE}"
run_pg pg_dump "${SOURCE_DATABASE_URL}" -Fc --no-owner --no-privileges -f "/workspace/${DUMP_FILE}"

echo "==> Restoring into target database"
run_pg pg_restore -d "${TARGET_DATABASE_URL}" --clean --if-exists --no-owner --no-privileges "/workspace/${DUMP_FILE}"

echo "==> Verifying row counts per table"
TABLES=$(run_pg psql "${SOURCE_DATABASE_URL}" -Atc \
  "select tablename from pg_tables where schemaname = 'public' order by tablename;")

MISMATCH=0
for TABLE in ${TABLES}; do
  SRC_COUNT=$(run_pg psql "${SOURCE_DATABASE_URL}" -Atc "select count(*) from \"${TABLE}\";")
  DST_COUNT=$(run_pg psql "${TARGET_DATABASE_URL}" -Atc "select count(*) from \"${TABLE}\";")
  if [[ "${SRC_COUNT}" == "${DST_COUNT}" ]]; then
    echo "  OK    ${TABLE}: ${SRC_COUNT} rows"
  else
    echo "  FAIL  ${TABLE}: source=${SRC_COUNT} target=${DST_COUNT}"
    MISMATCH=1
  fi
done

if [[ "${MISMATCH}" -ne 0 ]]; then
  echo "==> Row count mismatch detected — do NOT cut over yet. Re-run after checking for concurrent writes." >&2
  exit 1
fi

echo "==> All tables match. Dump kept at ${DUMP_FILE} (delete once you've confirmed the app works against Neon)."
echo "==> Next: point DATABASE_URL at Neon, redeploy, smoke-test, THEN decommission the Render database."
