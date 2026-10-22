"""Guards that let a migration run against a database already partly ahead of it.

Long-lived environments drift. Columns and tables get created out of band — a
metadata `create_all` during early development, a hotfix applied straight to
the database, a migration that half-succeeded — while the alembic version table
still points at an older revision.

A bare `op.add_column` or `op.create_table` then raises DuplicateColumn or
DuplicateTable, and alembic aborts the whole run *at that revision*. Every
later migration silently never executes. The damage is not the failed
statement, it is everything downstream of it: a missing `guide_videos` table
and a spread of 500s across unrelated admin endpoints all traced back to one
`is_on_vacation` column that already existed.

These helpers make the create-ish operations safe to re-run, so `upgrade head`
converges on the right schema instead of stopping at the first thing already
done. They deliberately do not touch drops or data migrations: skipping those
silently would hide real divergence rather than repair it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


def table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def column_exists(table: str, column: str) -> bool:
    if not table_exists(table):
        return False
    return column in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def index_exists(table: str, index: str) -> bool:
    if not table_exists(table):
        return False
    return index in {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if not column_exists(table, column.name):
        op.add_column(table, column)


def create_table_if_missing(table: str, *columns, **kwargs) -> None:
    if not table_exists(table):
        op.create_table(table, *columns, **kwargs)


def create_index_if_missing(index: str, table: str, columns, **kwargs) -> None:
    if not index_exists(table, index):
        op.create_index(index, table, columns, **kwargs)
