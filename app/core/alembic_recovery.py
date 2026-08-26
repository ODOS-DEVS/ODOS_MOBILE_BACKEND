"""Repair alembic_version when production DB is ahead of / out of sync with migration files."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.core.config import settings


def _schema_has_column(connection, table_name: str, column_name: str) -> bool:
    row = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = :table_name AND column_name = :column_name "
            "LIMIT 1"
        ),
        {"table_name": table_name, "column_name": column_name},
    ).fetchone()
    return row is not None


def _schema_has_table(connection, table_name: str) -> bool:
    row = connection.execute(
        text("SELECT to_regclass(:reg) IS NOT NULL"),
        {"reg": f"public.{table_name}"},
    ).scalar()
    return bool(row)


def _schema_has_promotion_engine(connection) -> bool:
    return _schema_has_column(connection, "vouchers", "promotion_type")


def _best_stamp_for_schema(connection, available: set[str], codebase_head: str) -> str:
    """Pick the newest known revision that matches what already exists in the DB."""
    candidates: list[tuple[str, bool]] = [
        ("r8s9t0u1v2w3", _schema_has_table(connection, "merchandising_campaigns")),
        ("o5p6q7r8s9t0", _schema_has_column(connection, "assistant_conversations", "context_json")),
        ("m3n4o5p6q7r8", _schema_has_column(connection, "saved_addresses", "gps_code")),
        ("l2m3n4o5p6q7", _schema_has_table(connection, "assistant_conversations")),
        ("k1l2m3n4o5p6", _schema_has_promotion_engine(connection)),
    ]
    for revision, present in candidates:
        if present and revision in available:
            return revision
    return ""


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

        target = _best_stamp_for_schema(connection, available, codebase_head)
        if not target:
            # Nothing in the schema identifies where this database actually is.
            # Stamping a guess (previously: the codebase head) marks every
            # migration applied without running it and corrupts the schema
            # silently. Leave the stamp alone and let alembic fail loudly.
            print(
                "Alembic recovery: database references missing revision "
                f"{current!r} and its schema matches no known revision; "
                "leaving alembic_version untouched for manual repair."
            )
            return
        print(
            "Alembic recovery: database references missing revision "
            f"{current!r}; stamping {target!r}"
        )
        connection.execute(
            text("UPDATE alembic_version SET version_num = :target"),
            {"target": target},
        )
        connection.commit()
