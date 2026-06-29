from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AdminDeliverySettingsRead(BaseModel):
    free_shipping_threshold: float
    economy_fee: float
    express_fee: float
    same_day_fee: float
    same_day_cutoff_hour: int
    same_day_regions: list[str]
    economy_enabled: bool
    express_enabled: bool
    same_day_enabled: bool
    economy_title: str
    economy_eta: str
    express_title: str
    express_eta: str
    same_day_title: str
    same_day_eta: str
    updated_at: datetime


class AdminDeliverySettingsUpdate(BaseModel):
    free_shipping_threshold: float = Field(ge=0)
    economy_fee: float = Field(ge=0)
    express_fee: float = Field(ge=0)
    same_day_fee: float = Field(ge=0)
    same_day_cutoff_hour: int = Field(ge=0, le=23)
    same_day_regions_text: str = Field(default="", max_length=4000)
    economy_enabled: bool = True
    express_enabled: bool = True
    same_day_enabled: bool = True
    economy_title: str = Field(min_length=1, max_length=80)
    economy_eta: str = Field(min_length=1, max_length=80)
    express_title: str = Field(min_length=1, max_length=80)
    express_eta: str = Field(min_length=1, max_length=80)
    same_day_title: str = Field(min_length=1, max_length=80)
    same_day_eta: str = Field(min_length=1, max_length=80)

    @field_validator(
        "economy_title",
        "economy_eta",
        "express_title",
        "express_eta",
        "same_day_title",
        "same_day_eta",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()
