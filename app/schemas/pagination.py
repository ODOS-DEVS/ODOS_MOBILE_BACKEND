from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class AdminPageRead(BaseModel, Generic[T]):
    items: list[T]
    has_more: bool
