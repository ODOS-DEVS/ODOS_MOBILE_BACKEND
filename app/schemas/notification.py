from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationEventRead(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    icon: str
    accent: str
    action_label: str | None
    route_type: str | None
    route_target_id: str | None
    image_key: str | None
    image_url: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationReadState(BaseModel):
    read_keys: list[str]


class NotificationReadUpdate(BaseModel):
    keys: list[str] = Field(default_factory=list, min_length=1)


class PushTokenUpdate(BaseModel):
    expo_push_token: str = Field(min_length=1, max_length=255)
