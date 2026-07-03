from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.event_types import ACTOR_ADMIN, ACTOR_ANONYMOUS, ACTOR_SYSTEM, ACTOR_USER
from app.models import SystemEventLog, User, UserRole
from app.schemas.event_log import EventLogCreate, EventLogRead, EventLogStatsRead
from app.services.realtime_service import realtime_manager

logger = logging.getLogger(__name__)

ADMIN_REALTIME_EVENT = "admin.event_log.created"


def _serialize_event(row: SystemEventLog) -> EventLogRead:
    return EventLogRead.model_validate(row)


def record_event(
    db: Session,
    *,
    event_type: str,
    actor_type: str,
    action: str,
    actor_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
    broadcast_admin: bool | None = None,
) -> SystemEventLog:
    row = SystemEventLog(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_json=metadata,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(row)

    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()

    should_broadcast = broadcast_admin
    if should_broadcast is None:
        should_broadcast = actor_type == ACTOR_ADMIN or event_type.startswith("system.")

    if should_broadcast:
        _broadcast_to_admins(db, _serialize_event(row))

    return row


def record_user_event(
    db: Session,
    *,
    user_id: str,
    event_type: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> SystemEventLog:
    return record_event(
        db,
        event_type=event_type,
        actor_type=ACTOR_USER,
        actor_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        user_agent=user_agent,
        commit=commit,
        broadcast_admin=False,
    )


def record_admin_event(
    db: Session,
    *,
    admin_user: User,
    event_type: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> SystemEventLog:
    enriched_metadata = dict(metadata or {})
    enriched_metadata.setdefault(
        "admin_permission",
        getattr(admin_user, "admin_permission", None) or "admin",
    )
    enriched_metadata.setdefault("admin_email", admin_user.email)

    return record_event(
        db,
        event_type=event_type,
        actor_type=ACTOR_ADMIN,
        actor_id=str(admin_user.id),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=enriched_metadata,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        user_agent=user_agent,
        commit=commit,
        broadcast_admin=True,
    )


def record_system_event(
    db: Session,
    *,
    event_type: str,
    action: str,
    metadata: dict[str, Any] | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> SystemEventLog:
    return record_event(
        db,
        event_type=event_type,
        actor_type=ACTOR_SYSTEM,
        actor_id=None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        commit=commit,
        broadcast_admin=True,
    )


def record_anonymous_security_event(
    db: Session,
    *,
    event_type: str,
    action: str,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> SystemEventLog:
    return record_event(
        db,
        event_type=event_type,
        actor_type=ACTOR_ANONYMOUS,
        actor_id=None,
        action=action,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        commit=commit,
        broadcast_admin=True,
    )


def list_event_logs(
    db: Session,
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
) -> tuple[list[EventLogRead], bool]:
    statement = select(SystemEventLog).order_by(SystemEventLog.created_at.desc())

    if event_type:
        statement = statement.where(SystemEventLog.event_type == event_type)
    if actor_type:
        statement = statement.where(SystemEventLog.actor_type == actor_type)
    if actor_id:
        statement = statement.where(SystemEventLog.actor_id == actor_id)
    if action:
        statement = statement.where(SystemEventLog.action == action)
    if entity_type:
        statement = statement.where(SystemEventLog.entity_type == entity_type)
    if entity_id:
        statement = statement.where(SystemEventLog.entity_id == entity_id)
    if since:
        statement = statement.where(SystemEventLog.created_at >= since)
    if until:
        statement = statement.where(SystemEventLog.created_at <= until)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            (SystemEventLog.action.ilike(pattern))
            | (SystemEventLog.event_type.ilike(pattern))
            | (SystemEventLog.entity_id.ilike(pattern))
        )

    rows = list(db.scalars(statement.offset(offset).limit(limit + 1)).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    return [_serialize_event(row) for row in page], has_more


def get_event_log_stats(db: Session) -> EventLogStatsRead:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)

    total_events = db.scalar(select(func.count(SystemEventLog.id))) or 0
    last_24h = db.scalar(
        select(func.count(SystemEventLog.id)).where(SystemEventLog.created_at >= since)
    ) or 0
    auth_failures_24h = db.scalar(
        select(func.count(SystemEventLog.id)).where(
            SystemEventLog.created_at >= since,
            SystemEventLog.event_type.in_(["user.login_failed", "system.auth_failure"]),
        )
    ) or 0
    rate_limits_24h = db.scalar(
        select(func.count(SystemEventLog.id)).where(
            SystemEventLog.created_at >= since,
            SystemEventLog.event_type == "system.rate_limit_triggered",
        )
    ) or 0
    admin_actions_24h = db.scalar(
        select(func.count(SystemEventLog.id)).where(
            SystemEventLog.created_at >= since,
            SystemEventLog.actor_type == ACTOR_ADMIN,
        )
    ) or 0
    security_events_24h = db.scalar(
        select(func.count(SystemEventLog.id)).where(
            SystemEventLog.created_at >= since,
            SystemEventLog.event_type.like("system.%"),
        )
    ) or 0

    return EventLogStatsRead(
        total_events=int(total_events),
        last_24h=int(last_24h),
        auth_failures_24h=int(auth_failures_24h),
        rate_limits_24h=int(rate_limits_24h),
        admin_actions_24h=int(admin_actions_24h),
        security_events_24h=int(security_events_24h),
    )


def _broadcast_to_admins(db: Session, event: EventLogRead) -> None:
    try:
        admin_ids = list(
            db.scalars(
                select(User.id).where(
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            ).all()
        )
        payload = event.model_dump(mode="json")
        for admin_id in admin_ids:
            realtime_manager.publish_user_event_sync(
                str(admin_id),
                ADMIN_REALTIME_EVENT,
                payload,
            )
    except Exception:
        logger.exception("Failed to broadcast admin event log")
