from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventLogCreate(BaseModel):
    event_type: str = Field(max_length=80)
    actor_type: str = Field(max_length=20)
    actor_id: str | None = Field(default=None, max_length=64)
    action: str = Field(max_length=120)
    entity_type: str | None = Field(default=None, max_length=80)
    entity_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    ip_address: str | None = Field(default=None, max_length=64)
    user_agent: str | None = None


class EventLogRead(BaseModel):
    id: uuid.UUID
    event_type: str
    actor_type: str
    actor_id: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    metadata: dict[str, Any] | None = Field(validation_alias="metadata_json")
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class EventLogPageRead(BaseModel):
    items: list[EventLogRead]
    has_more: bool


class EventLogStatsRead(BaseModel):
    total_events: int
    last_24h: int
    auth_failures_24h: int
    rate_limits_24h: int
    admin_actions_24h: int
    security_events_24h: int
