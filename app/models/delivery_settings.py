from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

DELIVERY_SETTINGS_ID = "default"


class DeliverySettings(Base):
    __tablename__ = "delivery_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=DELIVERY_SETTINGS_ID)
    free_shipping_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=299.0)
    economy_fee: Mapped[float] = mapped_column(Float, nullable=False, default=19.0)
    express_fee: Mapped[float] = mapped_column(Float, nullable=False, default=29.0)
    same_day_fee: Mapped[float] = mapped_column(Float, nullable=False, default=49.0)
    same_day_cutoff_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    same_day_regions: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)),
        nullable=False,
        default=list,
    )
    economy_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    express_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    same_day_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    economy_title: Mapped[str] = mapped_column(String(80), nullable=False, default="Standard delivery")
    economy_eta: Mapped[str] = mapped_column(String(80), nullable=False, default="3–5 business days")
    express_title: Mapped[str] = mapped_column(String(80), nullable=False, default="Express delivery")
    express_eta: Mapped[str] = mapped_column(String(80), nullable=False, default="1–2 business days")
    same_day_title: Mapped[str] = mapped_column(String(80), nullable=False, default="Same-day delivery")
    same_day_eta: Mapped[str] = mapped_column(String(80), nullable=False, default="Today")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
