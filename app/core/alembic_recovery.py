"""Repair alembic_version when production DB is ahead of deployed migration files."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.core.config import settings


def _schema_has_promotion_engine(connection) -> bool:
    row = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'vouchers' AND column_name = 'promotion_type' "
            "LIMIT 1"
        )
    ).fetchone()
    return row is not None


def recover_alembic_version() -> None:
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    available = {revision.revision for revision in script.walk_revisions()}
    heads = script.get_heads()
    if len(heads) != 1:
        return

    codebase_head = heads[0]
    engine = create_engine(settings.database_url, pool_pre_ping=True)

    with engine.connect() as connection:
        row = connection.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        if row is None:
            return

        current = str(row[0])
        has_promotion_schema = _schema_has_promotion_engine(connection)

        if current == "k1l2m3n4o5p6" and not has_promotion_schema and "j0k1l2m3n4o5" in available:
            print(
                "Alembic recovery: promotion migration marked applied but schema missing; "
                "stamping 'j0k1l2m3n4o5'"
            )
            connection.execute(
                text("UPDATE alembic_version SET version_num = :target"),
                {"target": "j0k1l2m3n4o5"},
            )
            connection.commit()
            return

        if current in available:
            return

        if has_promotion_schema and "k1l2m3n4o5p6" in available:
            target = "k1l2m3n4o5p6"
        else:
            target = codebase_head

        print(
            "Alembic recovery: database references missing revision "
            f"{current!r}; stamping {target!r}"
        )
        connection.execute(
            text("UPDATE alembic_version SET version_num = :target"),
            {"target": target},
        )
        connection.commit()
