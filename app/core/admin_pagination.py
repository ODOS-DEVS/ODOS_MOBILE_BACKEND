from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

DEFAULT_ADMIN_PAGE_SIZE = 30
MAX_ADMIN_PAGE_SIZE = 100

T = TypeVar("T")


def normalize_page_params(limit: int | None, offset: int | None) -> tuple[int, int]:
    resolved_limit = DEFAULT_ADMIN_PAGE_SIZE if limit is None else limit
    resolved_offset = 0 if offset is None else offset
    return max(1, min(resolved_limit, MAX_ADMIN_PAGE_SIZE)), max(0, resolved_offset)


def paginate_scalars(
    db: Session,
    statement: Select,
    *,
    limit: int,
    offset: int,
) -> tuple[list[T], bool]:
    limit, offset = normalize_page_params(limit, offset)
    rows = list(db.scalars(statement.offset(offset).limit(limit + 1)).all())
    has_more = len(rows) > limit
    return rows[:limit], has_more
