from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.admin_permissions import require_audit_access
from app.core.database import get_db
from app.models import User
from app.schemas.event_log import EventLogPageRead, EventLogStatsRead
from app.services.event_log_service import get_event_log_stats, list_event_logs


def get_admin_event_logs(
    db: Session,
    current_user: User,
    *,
    event_type: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> EventLogPageRead:
    _ = current_user
    items, has_more = list_event_logs(
        db,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        search=search,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return EventLogPageRead(items=items, has_more=has_more)


def get_admin_event_log_stats(db: Session, current_user: User) -> EventLogStatsRead:
    _ = current_user
    return get_event_log_stats(db)


def admin_event_log_list_dependency(
    current_user: Annotated[User, Depends(require_audit_access)],
    db: Annotated[Session, Depends(get_db)],
    event_type: Annotated[str | None, Query()] = None,
    actor_type: Annotated[str | None, Query()] = None,
    actor_id: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventLogPageRead:
    return get_admin_event_logs(
        db,
        current_user,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        search=search,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
