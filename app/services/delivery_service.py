from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.models.delivery_settings import DELIVERY_SETTINGS_ID, DeliverySettings

DeliveryMethodId = Literal["economy", "express", "same_day"]
ACCRA_TZ = ZoneInfo("Africa/Accra")

DEFAULT_SAME_DAY_REGIONS = (
    "greater accra",
    "accra",
    "tema",
    "madina",
    "ashaiman",
    "ga east",
    "ga west",
    "ga central",
    "ga south",
    "la dade kotopon",
    "ledzokuku",
    "krowor",
    "adenta",
    "ablekuma",
)


@dataclass(frozen=True)
class DeliveryConfig:
    free_shipping_threshold: float = 299.0
    economy_fee: float = 19.0
    express_fee: float = 29.0
    same_day_fee: float = 49.0
    same_day_cutoff_hour: int = 14
    same_day_regions: tuple[str, ...] = DEFAULT_SAME_DAY_REGIONS
    economy_enabled: bool = True
    express_enabled: bool = True
    same_day_enabled: bool = True
    economy_title: str = "Standard delivery"
    economy_eta: str = "3–5 business days"
    express_title: str = "Express delivery"
    express_eta: str = "1–2 business days"
    same_day_title: str = "Same-day delivery"
    same_day_eta: str = "Today"


DEFAULT_DELIVERY_CONFIG = DeliveryConfig()


@dataclass(frozen=True)
class DeliveryOption:
    id: DeliveryMethodId
    title: str
    subtitle: str
    eta: str
    amount: float
    available: bool
    badge: str | None = None
    unavailable_reason: str | None = None


def delivery_config_from_settings(settings: DeliverySettings | None) -> DeliveryConfig:
    if settings is None:
        return DEFAULT_DELIVERY_CONFIG

    regions = tuple(
        region.strip().lower()
        for region in (settings.same_day_regions or [])
        if region and region.strip()
    )
    return DeliveryConfig(
        free_shipping_threshold=float(settings.free_shipping_threshold),
        economy_fee=float(settings.economy_fee),
        express_fee=float(settings.express_fee),
        same_day_fee=float(settings.same_day_fee),
        same_day_cutoff_hour=int(settings.same_day_cutoff_hour),
        same_day_regions=regions or DEFAULT_SAME_DAY_REGIONS,
        economy_enabled=bool(settings.economy_enabled),
        express_enabled=bool(settings.express_enabled),
        same_day_enabled=bool(settings.same_day_enabled),
        economy_title=settings.economy_title.strip() or "Standard delivery",
        economy_eta=settings.economy_eta.strip() or "3–5 business days",
        express_title=settings.express_title.strip() or "Express delivery",
        express_eta=settings.express_eta.strip() or "1–2 business days",
        same_day_title=settings.same_day_title.strip() or "Same-day delivery",
        same_day_eta=settings.same_day_eta.strip() or "Today",
    )


def get_delivery_settings_row(db: Session) -> DeliverySettings:
    row = db.scalar(select(DeliverySettings).where(DeliverySettings.id == DELIVERY_SETTINGS_ID))
    if row is not None:
        return row

    row = DeliverySettings(
        id=DELIVERY_SETTINGS_ID,
        same_day_regions=list(DEFAULT_SAME_DAY_REGIONS),
    )
    db.add(row)
    db.flush()
    return row


def get_delivery_config(db: Session | None) -> DeliveryConfig:
    if db is None:
        return DEFAULT_DELIVERY_CONFIG
    return delivery_config_from_settings(get_delivery_settings_row(db))


def parse_same_day_regions(raw: str) -> list[str]:
    values: list[str] = []
    for chunk in raw.replace("\r", "\n").split("\n"):
        for part in chunk.split(","):
            normalized = part.strip().lower()
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def is_same_day_eligible_region(region: str | None, config: DeliveryConfig) -> bool:
    normalized = (region or "").strip().lower()
    if not normalized:
        return False
    return any(
        alias == normalized or alias in normalized for alias in config.same_day_regions
    )


def is_same_day_order_window_open(
    config: DeliveryConfig,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(ACCRA_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=ACCRA_TZ)
    else:
        current = current.astimezone(ACCRA_TZ)
    if current.weekday() == 6:
        return False
    return current.hour < config.same_day_cutoff_hour


def build_delivery_options(
    *,
    subtotal: float,
    region: str | None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
    now: datetime | None = None,
) -> list[DeliveryOption]:
    same_day_region_ok = is_same_day_eligible_region(region, config)
    same_day_window_ok = is_same_day_order_window_open(config, now)
    same_day_available = (
        config.same_day_enabled and same_day_region_ok and same_day_window_ok
    )

    economy_amount = (
        0.0 if subtotal >= config.free_shipping_threshold else config.economy_fee
    )
    economy_subtitle = (
        f"Free shipping on orders over GH₵{config.free_shipping_threshold:.0f}"
        if economy_amount == 0
        else "Reliable nationwide delivery with tracking updates"
    )

    if not config.same_day_enabled:
        same_day_reason = "Same-day delivery is temporarily unavailable"
        same_day_subtitle = "Check back later or choose express delivery"
        same_day_eta = "Unavailable"
    elif not same_day_region_ok:
        same_day_reason = "Select a Greater Accra address at checkout"
        same_day_subtitle = "Available when your delivery address is in Greater Accra"
        same_day_eta = "Greater Accra only"
    elif not same_day_window_ok:
        same_day_reason = f"Same-day orders close at {config.same_day_cutoff_hour}:00 (Mon–Sat)"
        same_day_subtitle = f"Order before {config.same_day_cutoff_hour}:00 for evening drop-off"
        same_day_eta = "Unavailable today"
    else:
        same_day_reason = None
        same_day_subtitle = f"Order before {config.same_day_cutoff_hour}:00 for evening drop-off"
        same_day_eta = config.same_day_eta

    options: list[DeliveryOption] = []

    if config.economy_enabled:
        options.append(
            DeliveryOption(
                id="economy",
                title=config.economy_title,
                subtitle=economy_subtitle,
                eta=config.economy_eta,
                amount=economy_amount,
                badge="Free" if economy_amount == 0 else None,
                available=True,
            )
        )

    if config.express_enabled:
        options.append(
            DeliveryOption(
                id="express",
                title=config.express_title,
                subtitle="Priority dispatch from the seller warehouse",
                eta=config.express_eta,
                amount=config.express_fee,
                available=True,
            )
        )

    if config.same_day_enabled:
        options.append(
            DeliveryOption(
                id="same_day",
                title=config.same_day_title,
                subtitle=same_day_subtitle,
                eta=same_day_eta,
                amount=config.same_day_fee,
                available=same_day_available,
                unavailable_reason=same_day_reason,
            )
        )

    if not options:
        options.append(
            DeliveryOption(
                id="economy",
                title=config.economy_title,
                subtitle=economy_subtitle,
                eta=config.economy_eta,
                amount=economy_amount,
                badge="Free" if economy_amount == 0 else None,
                available=True,
            )
        )

    return options


def resolve_active_delivery_method(
    options: list[DeliveryOption],
    method_id: DeliveryMethodId,
) -> DeliveryMethodId:
    selected = next((option for option in options if option.id == method_id), None)
    if selected and selected.available:
        return method_id
    fallback = next((option for option in options if option.available), None)
    return fallback.id if fallback else "economy"


def resolve_delivery_amount(
    options: list[DeliveryOption],
    method_id: DeliveryMethodId,
) -> float:
    selected = next((option for option in options if option.id == method_id), None)
    if selected and selected.available:
        return round(selected.amount, 2)

    active_id = resolve_active_delivery_method(options, method_id)
    active = next((option for option in options if option.id == active_id), None)
    return round(active.amount, 2) if active else 0.0


def quote_delivery(
    *,
    subtotal: float,
    region: str | None,
    selected_method: DeliveryMethodId = "economy",
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
    now: datetime | None = None,
) -> dict[str, object]:
    options = build_delivery_options(
        subtotal=subtotal,
        region=region,
        config=config,
        now=now,
    )
    active_method = resolve_active_delivery_method(options, selected_method)
    shipping_amount = resolve_delivery_amount(options, active_method)

    return {
        "options": options,
        "selected_method": active_method,
        "shipping_amount": shipping_amount,
        "free_shipping_threshold": config.free_shipping_threshold,
        "same_day_cutoff_passed": is_same_day_eligible_region(region, config)
        and not is_same_day_order_window_open(config, now),
    }


def validate_delivery_checkout(
    *,
    subtotal: float,
    region: str,
    delivery_method: DeliveryMethodId | None,
    shipping_amount: float,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> tuple[DeliveryMethodId, float]:
    quote = quote_delivery(
        subtotal=subtotal,
        region=region,
        selected_method=delivery_method or "economy",
        config=config,
    )
    expected_method = quote["selected_method"]
    expected_amount = float(quote["shipping_amount"])
    if abs(shipping_amount - expected_amount) > 0.01:
        raise ValueError(
            "Delivery fee changed. Refresh checkout and choose your delivery option again."
        )
    return expected_method, expected_amount


def delivery_method_label(method: str | None, config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG) -> str:
    normalized = (method or "economy").strip().lower()
    if normalized == "express":
        return config.express_title
    if normalized == "same_day":
        return config.same_day_title
    return config.economy_title


def tracking_eta_after_payment(
    method: str | None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> str:
    normalized = (method or "economy").strip().lower()
    if normalized == "same_day":
        return f"{config.same_day_title} · arriving today"
    if normalized == "express":
        return f"{config.express_title} · estimated {config.express_eta.lower()}"
    return f"{config.economy_title} · estimated {config.economy_eta.lower()}"


def tracking_eta_for_vendor_status(
    vendor_status: str,
    delivery_method: str | None = None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> str | None:
    if vendor_status == "out_for_delivery":
        return "Out for delivery · on the way to you"
    eta_map = {
        "pending": "Awaiting vendor confirmation",
        "confirmed": "Confirmed by vendor",
        "processing": "Being prepared for dispatch",
        "ready": "Ready for delivery handoff",
    }
    base = eta_map.get(vendor_status)
    if not base:
        return None
    method = (delivery_method or "economy").strip().lower()
    if method == "same_day" and vendor_status == "ready":
        return "Ready for same-day drop-off"
    return base
