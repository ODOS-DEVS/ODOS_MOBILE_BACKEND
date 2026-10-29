"""Rewrite a migration's upgrade() to use the idempotent guards.

Only `upgrade()` is touched. `downgrade()` keeps its bare `op.*` calls on
purpose: a rollback that silently skips a drop because the object is "already
gone" would leave the schema in a state nobody described.

Usage:  .venv/bin/python scripts/make_migration_idempotent.py <revision-id> ...
"""

from __future__ import annotations

import glob
import re
import sys

REPLACEMENTS = {
    "op.create_table(": "create_table_if_missing(",
    "op.add_column(": "add_column_if_missing(",
    "op.create_index(": "create_index_if_missing(",
}

IMPORT_LINE = (
    "from app.core.migration_guards import (\n"
    "    add_column_if_missing,\n"
    "    create_index_if_missing,\n"
    "    create_table_if_missing,\n"
    ")\n"
)


def patch(path: str) -> bool:
    with open(path) as handle:
        src = handle.read()
    if "app.core.migration_guards" in src:
        return False

    start = src.find("def upgrade()")
    if start == -1:
        return False
    end = src.find("def downgrade()", start)
    if end == -1:
        end = len(src)

    head, body, tail = src[:start], src[start:end], src[end:]
    changed = False
    for old, new in REPLACEMENTS.items():
        if old in body:
            body = body.replace(old, new)
            changed = True
    if not changed:
        return False

    # Import goes after the last existing top-level import.
    imports = list(re.finditer(r"^(?:from|import) .+$", head, re.M))
    if imports:
        at = imports[-1].end()
        head = head[:at] + "\n" + IMPORT_LINE.rstrip() + head[at:]
    else:
        head = IMPORT_LINE + head

    with open(path, "w") as handle:
        handle.write(head + body + tail)
    return True


def main() -> None:
    for revision in sys.argv[1:]:
        matches = glob.glob(f"alembic/versions/{revision}*.py")
        if not matches:
            print(f"{revision}: NOT FOUND")
            continue
        for path in matches:
            print(f"{revision}: {'patched' if patch(path) else 'no change'}")


if __name__ == "__main__":
    main()
