"""CLI wrapper for alembic recovery (used by Render startup script)."""

from app.core.alembic_recovery import recover_alembic_version


if __name__ == "__main__":
    recover_alembic_version()
