"""Repair alembic_version when production DB is ahead of deployed migration files."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.core.config import settings


def _schema_has_column(connection, table_name: str, column_name: str) -> bool:
    row = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table_name AND column_name = :column_name "
            "LIMIT 1"
        ),
        {"table_name": table_name, "column_name": column_name},
    ).fetchone()
    return row is not None


def _schema_has_table(connection, table_name: str) -> bool:
    row = connection.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :table_name "
            "LIMIT 1"
        ),
        {"table_name": table_name},
    ).fetchone()
    return row is not None


def _schema_has_promotion_engine(connection) -> bool:
    return _schema_has_column(connection, "vouchers", "promotion_type")


def _best_stamp_for_schema(connection, available: set[str], codebase_head: str) -> str:
    """Pick the newest known revision that matches what already exists in the DB."""
    # Newest first — stamp as far forward as the live schema already reflects.
    candidates: list[tuple[str, bool]] = [
        ("r8s9t0u1v2w3", _schema_has_table(connection, "merchandising_campaigns")),
        ("o5p6q7r8s9t0", _schema_has_column(connection, "assistant_conversations", "context_json")),
        ("l2m3n4o5p6q7", _schema_has_table(connection, "assistant_conversations")),
        ("k1l2m3n4o5p6", _schema_has_promotion_engine(connection)),
    ]
    for revision, present in candidates:
        if present and revision in available:
            return revision
    return codebase_head if codebase_head in available else codebase_head


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

        # If assistant tables already exist but version is still before that migration,
        # stamp forward so upgrade does not try to recreate them.
        if (
            current in available
            and current in {"k1l2m3n4o5p6", "j0k1l2m3n4o5"}
            and _schema_has_table(connection, "assistant_conversations")
            and "l2m3n4o5p6q7" in available
        ):
            target = _best_stamp_for_schema(connection, available, codebase_head)
            if target != current:
                print(
                    "Alembic recovery: assistant schema already present; "
                    f"advancing stamp from {current!r} to {target!r}"
                )
                connection.execute(
                    text("UPDATE alembic_version SET version_num = :target"),
                    {"target": target},
                )
                connection.commit()
            return

        if current in available:
            return

        target = _best_stamp_for_schema(connection, available, codebase_head)
        print(
            "Alembic recovery: database references missing revision "
            f"{current!r}; stamping {target!r}"
        )
        connection.execute(
            text("UPDATE alembic_version SET version_num = :target"),
            {"target": target},
        )
        connection.commit()
