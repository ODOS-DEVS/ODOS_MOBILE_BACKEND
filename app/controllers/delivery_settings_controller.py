from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.controllers.admin_controller import require_admin
from app.models import User
from app.models.delivery_settings import DeliverySettings
from app.schemas.delivery_settings import (
    AdminDeliverySettingsRead,
    AdminDeliverySettingsUpdate,
)
from app.services.delivery_service import (
    get_delivery_settings_row,
    parse_same_day_regions,
)


def _serialize_delivery_settings(row: DeliverySettings) -> AdminDeliverySettingsRead:
    return AdminDeliverySettingsRead(
        free_shipping_threshold=row.free_shipping_threshold,
        economy_fee=row.economy_fee,
        express_fee=row.express_fee,
        same_day_fee=row.same_day_fee,
        same_day_cutoff_hour=row.same_day_cutoff_hour,
        same_day_regions=list(row.same_day_regions or []),
        economy_enabled=row.economy_enabled,
        express_enabled=row.express_enabled,
        same_day_enabled=row.same_day_enabled,
        economy_title=row.economy_title,
        economy_eta=row.economy_eta,
        express_title=row.express_title,
        express_eta=row.express_eta,
        same_day_title=row.same_day_title,
        same_day_eta=row.same_day_eta,
        updated_at=row.updated_at,
    )


def get_admin_delivery_settings(db: Session, current_user: User) -> AdminDeliverySettingsRead:
    require_admin(current_user)
    row = get_delivery_settings_row(db)
    return _serialize_delivery_settings(row)


def update_admin_delivery_settings(
    db: Session,
    current_user: User,
    payload: AdminDeliverySettingsUpdate,
) -> AdminDeliverySettingsRead:
    require_admin(current_user)

    if payload.same_day_cutoff_hour < 0 or payload.same_day_cutoff_hour > 23:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Same-day cutoff hour must be between 0 and 23.",
        )

    regions = parse_same_day_regions(payload.same_day_regions_text)
    if payload.same_day_enabled and not regions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one same-day region when same-day delivery is enabled.",
        )

    row = get_delivery_settings_row(db)
    row.free_shipping_threshold = round(payload.free_shipping_threshold, 2)
    row.economy_fee = round(payload.economy_fee, 2)
    row.express_fee = round(payload.express_fee, 2)
    row.same_day_fee = round(payload.same_day_fee, 2)
    row.same_day_cutoff_hour = payload.same_day_cutoff_hour
    row.same_day_regions = regions
    row.economy_enabled = payload.economy_enabled
    row.express_enabled = payload.express_enabled
    row.same_day_enabled = payload.same_day_enabled
    row.economy_title = payload.economy_title.strip()
    row.economy_eta = payload.economy_eta.strip()
    row.express_title = payload.express_title.strip()
    row.express_eta = payload.express_eta.strip()
    row.same_day_title = payload.same_day_title.strip()
    row.same_day_eta = payload.same_day_eta.strip()

    db.commit()
    db.refresh(row)
    return _serialize_delivery_settings(row)
