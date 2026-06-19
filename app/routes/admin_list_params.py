from typing import Annotated

from fastapi import Depends, Query

from app.core.admin_pagination import DEFAULT_ADMIN_PAGE_SIZE, MAX_ADMIN_PAGE_SIZE


def admin_list_params(
    limit: int = Query(DEFAULT_ADMIN_PAGE_SIZE, ge=1, le=MAX_ADMIN_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> tuple[int, int]:
    return limit, offset


AdminListParams = Annotated[tuple[int, int], Depends(admin_list_params)]
