"""Centralized voucher validation and discount quoting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, Product, Store, User, Voucher, VoucherAssignment, VoucherRedemption
from app.schemas.order import OrderItemCreate
from app.services.finance_math import round_money
from app.services.pricing_service import resolve_cart_line_prices

SUPPORTED_VOUCHER_DISCOUNT_TYPES = {"percent", "fixed", "free_shipping"}
SUPPORTED_VOUCHER_SCOPES = {"odos", "store", "category", "product"}
SUPPORTED_VOUCHER_AVAILABILITY = {"auto", "claim", "assigned", "private"}
PUBLIC_VOUCHER_AVAILABILITY = {"auto", "claim"}
APPROVED_VOUCHER_STATUS = "approved"


@dataclass(slots=True)
class VoucherQuote:
    voucher: Voucher
    discount_amount: float
    eligible_subtotal_amount: float
    subtotal_amount: float
    shipping_amount: float
    total_amount: float
    store_name: str | None = None


def normalize_voucher_code(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def build_voucher_reward_text(discount_type: str, discount_value: float) -> str:
    if discount_type == "percent":
        value = int(discount_value) if float(discount_value).is_integer() else round(discount_value, 2)
        return f"{value}% OFF"
    if discount_type == "fixed":
        value = int(discount_value) if float(discount_value).is_integer() else round(discount_value, 2)
        return f"GH₵{value} OFF"
    return "FREE DELIVERY"


def voucher_status(voucher: Voucher, *, now: datetime, overall_count: int) -> str:
    if not voucher.is_active:
        return "disabled"
    if getattr(voucher, "approval_status", "approved") != APPROVED_VOUCHER_STATUS:
        return "pending_review"
    if voucher.starts_at and voucher.starts_at > now:
        return "scheduled"
    if voucher.ends_at and voucher.ends_at < now:
        return "expired"
    if voucher.usage_limit is not None and overall_count >= voucher.usage_limit:
        return "limit_reached"
    return "active"


def _counts_for_voucher(db: Session, voucher_id: uuid.UUID, user_id: uuid.UUID) -> tuple[int, int]:
    overall = db.scalar(
        select(func.count(VoucherRedemption.id)).where(VoucherRedemption.voucher_id == voucher_id)
    )
    user_count = db.scalar(
        select(func.count(VoucherRedemption.id)).where(
            VoucherRedemption.voucher_id == voucher_id,
            VoucherRedemption.user_id == user_id,
        )
    )
    pending_count = db.scalar(
        select(func.count(Order.id)).where(
            Order.voucher_id == voucher_id,
            Order.user_id == user_id,
            Order.status == "pending_payment",
        )
    )
    return int(overall or 0), int(user_count or 0) + int(pending_count or 0)


def _assignment_for_user(db: Session, voucher_id: uuid.UUID, user_id: uuid.UUID) -> VoucherAssignment | None:
    return db.scalar(
        select(VoucherAssignment).where(
            VoucherAssignment.voucher_id == voucher_id,
            VoucherAssignment.user_id == user_id,
        )
    )


def _product_metadata_map(db: Session, items: list[OrderItemCreate]) -> dict[str, dict[str, object]]:
    product_ids = [item.product_id for item in items]
    if not product_ids:
        return {}

    rows = db.execute(
        select(
            Product.id,
            Product.store_id,
            Product.category_slugs,
            Product.category,
        ).where(Product.id.in_(product_ids))
    ).all()
    return {
        product_id: {
            "store_id": store_id,
            "category_slugs": category_slugs or [],
            "category": category,
        }
        for product_id, store_id, category_slugs, category in rows
    }


def _line_is_eligible(
    voucher: Voucher,
    item: OrderItemCreate,
    *,
    product_meta: dict[str, object],
    unit_price: float,
) -> bool:
    product_id = item.product_id
    excluded = set(getattr(voucher, "excluded_product_ids", None) or [])
    if product_id in excluded:
        return False

    allowed_products = set(getattr(voucher, "product_ids", None) or [])
    if allowed_products and product_id not in allowed_products:
        return False

    store_id = product_meta.get("store_id")
    if voucher.scope == "store":
        return store_id == voucher.store_id

    if voucher.scope == "product":
        return product_id in allowed_products

    if voucher.scope == "category":
        category_slugs = set(getattr(voucher, "category_slugs", None) or [])
        if not category_slugs:
            return True
        product_slugs = set(product_meta.get("category_slugs") or [])
        normalized_category = str(product_meta.get("category") or "").strip().lower()
        if normalized_category:
            product_slugs.add(normalized_category.replace(" ", "-"))
        return bool(product_slugs.intersection(category_slugs))

    return True


def eligible_subtotal_for_voucher(
    voucher: Voucher,
    items: list[OrderItemCreate],
    *,
    product_meta_map: dict[str, dict[str, object]],
    line_prices: dict[str, float],
) -> float:
    total = 0.0
    for item in items:
        meta = product_meta_map.get(item.product_id, {})
        unit_price = line_prices.get(item.product_id, float(item.unit_price))
        if _line_is_eligible(voucher, item, product_meta=meta, unit_price=unit_price):
            total += unit_price * item.quantity
    return round_money(total)


def discount_for_voucher(
    voucher: Voucher,
    eligible_subtotal: float,
    shipping_amount: float,
) -> float:
    if voucher.discount_type == "percent":
        discount = eligible_subtotal * (voucher.discount_value / 100)
    elif voucher.discount_type == "fixed":
        discount = voucher.discount_value
    elif voucher.discount_type == "free_shipping":
        discount = shipping_amount
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="That promo code is configured incorrectly.",
        )

    if voucher.max_discount is not None:
        discount = min(discount, voucher.max_discount)

    ceiling = eligible_subtotal + (shipping_amount if voucher.discount_type == "free_shipping" else 0)
    return round_money(max(0, min(discount, ceiling)))


def _user_has_prior_orders(db: Session, user_id: uuid.UUID) -> bool:
    existing = db.scalar(
        select(Order.id).where(
            Order.user_id == user_id,
            Order.status.in_(("paid", "processing", "shipped", "delivered", "completed")),
        )
    )
    return existing is not None


def _raise_voucher_error(message: str, *, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
    raise HTTPException(status_code=status_code, detail=message)


def validate_voucher_for_checkout(
    db: Session,
    user: User,
    voucher: Voucher,
    items: list[OrderItemCreate],
    shipping_amount: float,
) -> VoucherQuote:
    now = datetime.now(timezone.utc)
    overall_count, user_count = _counts_for_voucher(db, voucher.id, user.id)
    current_status = voucher_status(voucher, now=now, overall_count=overall_count)
    assignment = _assignment_for_user(db, voucher.id, user.id)

    if current_status == "pending_review":
        _raise_voucher_error("This promo code is still being reviewed.")
    if current_status == "scheduled":
        _raise_voucher_error("This voucher is not active yet.")
    if current_status == "expired":
        _raise_voucher_error("This voucher has expired.")
    if current_status == "limit_reached":
        _raise_voucher_error("This voucher has reached its usage limit.")
    if current_status == "disabled":
        _raise_voucher_error("This voucher is no longer available.")

    if voucher.availability == "assigned" and assignment is None:
        _raise_voucher_error(
            "This promotion is only available to shoppers it was gifted to.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if voucher.availability == "claim" and assignment is None:
        _raise_voucher_error("Save this promo to your wallet before using it at checkout.")

    if voucher.per_user_limit is not None and user_count >= voucher.per_user_limit:
        _raise_voucher_error("This voucher has already been used.")

    if getattr(voucher, "first_order_only", False) and _user_has_prior_orders(db, user.id):
        _raise_voucher_error("This voucher is only valid on your first order.")

    if getattr(voucher, "new_user_only", False):
        account_age_days = (now - user.created_at).days if user.created_at else 0
        if account_age_days > 30:
            _raise_voucher_error("This voucher is only available to new ODOS shoppers.")

    pricing = resolve_cart_line_prices(db, items)
    line_prices = {
        product_id: price.sale_price for product_id, price in pricing.items()
    }
    subtotal_amount = round_money(
        sum(line_prices.get(item.product_id, item.unit_price) * item.quantity for item in items)
    )
    product_meta_map = _product_metadata_map(db, items)
    eligible_subtotal_amount = eligible_subtotal_for_voucher(
        voucher,
        items,
        product_meta_map=product_meta_map,
        line_prices=line_prices,
    )

    store_name = None
    if voucher.store_id:
        store_name = db.scalar(select(Store.title).where(Store.id == voucher.store_id))

    if voucher.scope in {"store", "product", "category"} and eligible_subtotal_amount <= 0:
        if voucher.scope == "store":
            label = store_name or "that store"
            _raise_voucher_error(f"This voucher only applies to items from {label}.")
        if voucher.scope == "product":
            _raise_voucher_error("This voucher does not apply to items in your cart.")
        _raise_voucher_error("This voucher does not apply to items in your cart.")

    if eligible_subtotal_amount < voucher.min_subtotal:
        shortfall = round_money(voucher.min_subtotal - eligible_subtotal_amount)
        _raise_voucher_error(
            f"Add at least GH₵{voucher.min_subtotal:.2f} worth of eligible items to use this voucher. "
            f"You need GH₵{shortfall:.2f} more."
        )

    discount_amount = discount_for_voucher(voucher, eligible_subtotal_amount, shipping_amount)
    total_amount = round_money(max(0, subtotal_amount + shipping_amount - discount_amount))

    return VoucherQuote(
        voucher=voucher,
        discount_amount=discount_amount,
        eligible_subtotal_amount=eligible_subtotal_amount,
        subtotal_amount=subtotal_amount,
        shipping_amount=round_money(shipping_amount),
        total_amount=total_amount,
        store_name=store_name,
    )


def is_voucher_publicly_listable(voucher: Voucher) -> bool:
    visibility = getattr(voucher, "visibility", "public")
    if visibility != "public":
        return False
    if voucher.availability not in PUBLIC_VOUCHER_AVAILABILITY:
        return False
    if getattr(voucher, "approval_status", APPROVED_VOUCHER_STATUS) != APPROVED_VOUCHER_STATUS:
        return False
    return True
