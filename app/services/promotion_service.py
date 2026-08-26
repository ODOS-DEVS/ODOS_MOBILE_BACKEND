"""Centralized voucher validation and discount quoting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Order, Product, Store, User, Voucher, VoucherAssignment, VoucherRedemption
from app.schemas.order import OrderItemCreate
from app.services.finance_math import round_money
from app.services.pricing_service import resolve_cart_line_prices

PROMOTION_TYPES = frozenset(
    {"coupon", "automatic", "product", "cart", "bogo", "free_shipping"}
)
SUPPORTED_VOUCHER_DISCOUNT_TYPES = {"percent", "fixed", "free_shipping", "bogo"}
SUPPORTED_VOUCHER_SCOPES = {"odos", "store", "category", "product"}
SUPPORTED_VOUCHER_OWNER_TYPES = {"platform", "vendor"}
SUPPORTED_VOUCHER_AVAILABILITY = {"auto", "claim", "assigned", "private"}
PUBLIC_VOUCHER_AVAILABILITY = {"auto", "claim"}
APPROVED_VOUCHER_STATUS = "approved"
# Lifecycle mapped onto existing fields:
# draft -> pending approval / inactive
# active -> computed active
# paused -> is_active=false while still approved
# expired -> ends_at passed
# archived -> soft-disabled via archive endpoints


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


def build_voucher_reward_text(
    discount_type: str,
    discount_value: float,
    *,
    promotion_type: str = "coupon",
    bogo_buy_quantity: int | None = None,
    bogo_get_quantity: int | None = None,
    bogo_get_discount_percent: float | None = None,
) -> str:
    if promotion_type == "bogo" or discount_type == "bogo":
        if bogo_buy_quantity and bogo_get_quantity:
            pct = bogo_get_discount_percent or 100
            if pct >= 100:
                return f"BUY {bogo_buy_quantity} GET {bogo_get_quantity} FREE"
            else:
                pct_val = int(pct) if float(pct).is_integer() else round(pct, 1)
                return f"BUY {bogo_buy_quantity} GET {bogo_get_quantity} {pct_val}% OFF"
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


def reserve_voucher_usage(db: Session, user_id: uuid.UUID, voucher: Voucher) -> None:
    """Row-lock + recount immediately before order persistence, closing the TOCTOU
    window between the unlocked check in validate_voucher_for_checkout and the
    locked recheck in activate_order_after_payment. No-ops for unlimited vouchers."""
    if voucher.per_user_limit is None and voucher.usage_limit is None:
        return
    try:
        db.execute(text("SET LOCAL lock_timeout = '3000ms'"))
        locked = db.scalar(select(Voucher).where(Voucher.id == voucher.id).with_for_update())
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This promo is in high demand right now. Please try again in a moment.",
        ) from exc
    if locked is None:
        return
    now = datetime.now(timezone.utc)
    overall_count, user_count = _counts_for_voucher(db, locked.id, user_id)
    current_status = voucher_status(locked, now=now, overall_count=overall_count)
    if current_status in {"expired", "limit_reached", "disabled", "pending_review"}:
        _raise_voucher_error("This voucher is no longer available.")
    if locked.per_user_limit is not None and user_count >= locked.per_user_limit:
        _raise_voucher_error("This voucher has already been used.")


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


def _product_category_slugs(product_meta: dict[str, object]) -> set[str]:
    product_slugs = {
        str(slug).strip().lower()
        for slug in (product_meta.get("category_slugs") or [])
        if str(slug).strip()
    }
    normalized_category = str(product_meta.get("category") or "").strip().lower()
    if normalized_category:
        product_slugs.add(normalized_category.replace(" ", "-"))
    return product_slugs


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

    product_slugs = _product_category_slugs(product_meta)
    excluded_categories = {
        str(slug).strip().lower()
        for slug in (getattr(voucher, "excluded_category_slugs", None) or [])
        if str(slug).strip()
    }
    if excluded_categories and product_slugs.intersection(excluded_categories):
        return False

    allowed_products = set(getattr(voucher, "product_ids", None) or [])
    if allowed_products and product_id not in allowed_products:
        return False

    store_id = product_meta.get("store_id")
    eligible_stores = set(getattr(voucher, "eligible_store_ids", None) or [])
    if eligible_stores and store_id not in eligible_stores:
        return False

    owner_type = getattr(voucher, "owner_type", None) or (
        "vendor" if voucher.scope == "store" and voucher.store_id else "platform"
    )
    if owner_type == "vendor":
        # Vendor vouchers may never discount another store's products.
        if not voucher.store_id or store_id != voucher.store_id:
            return False

    if voucher.scope == "store":
        return store_id == voucher.store_id

    if voucher.scope == "product":
        return bool(allowed_products) and product_id in allowed_products

    if voucher.scope == "category":
        category_slugs = {
            str(slug).strip().lower()
            for slug in (getattr(voucher, "category_slugs", None) or [])
            if str(slug).strip()
        }
        if not category_slugs:
            return True
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


def _discount_cap(voucher: Voucher) -> float | None:
    """The maximum discount this voucher may grant, or None for uncapped.

    A stored cap of 0 (or negative) is treated as "no cap", not as "grant
    nothing". The column is nullable and the admin/vendor forms accept `ge=0`,
    so a cap left at 0 rather than blank is the normal way an uncapped voucher
    gets saved — and `min(discount, 0)` silently turned those into dead codes
    that were still accepted at checkout, still counted against usage limits,
    and still written to voucher_redemptions, while charging the customer full
    price. Nobody deliberately configures a promotion that discounts nothing.
    """
    cap = voucher.max_discount
    if cap is None or cap <= 0:
        return None
    return cap


def discount_for_voucher(
    voucher: Voucher,
    eligible_subtotal: float,
    shipping_amount: float,
    *,
    items: list[OrderItemCreate] | None = None,
    product_meta_map: dict[str, dict[str, object]] | None = None,
    line_prices: dict[str, float] | None = None,
) -> float:
    promotion_type = getattr(voucher, "promotion_type", None) or "coupon"

    if promotion_type == "bogo" or voucher.discount_type == "bogo":
        return _bogo_discount_for_voucher(
            voucher,
            items or [],
            product_meta_map=product_meta_map or {},
            line_prices=line_prices or {},
        )

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

    cap = _discount_cap(voucher)
    if cap is not None:
        discount = min(discount, cap)

    ceiling = eligible_subtotal + (shipping_amount if voucher.discount_type == "free_shipping" else 0)
    return round_money(max(0, min(discount, ceiling)))


def _bogo_discount_for_voucher(
    voucher: Voucher,
    items: list[OrderItemCreate],
    *,
    product_meta_map: dict[str, dict[str, object]],
    line_prices: dict[str, float],
) -> float:
    buy_qty = int(getattr(voucher, "bogo_buy_quantity", None) or 0)
    get_qty = int(getattr(voucher, "bogo_get_quantity", None) or 0)
    get_percent = float(getattr(voucher, "bogo_get_discount_percent", None) or 100)

    if buy_qty <= 0 or get_qty <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This BOGO promotion is configured incorrectly.",
        )

    unit_prices: list[float] = []
    for item in items:
        meta = product_meta_map.get(item.product_id, {})
        unit_price = line_prices.get(item.product_id, float(item.unit_price))
        if not _line_is_eligible(voucher, item, product_meta=meta, unit_price=unit_price):
            continue
        unit_prices.extend([unit_price] * item.quantity)

    if not unit_prices:
        return 0.0

    unit_prices.sort()
    bundle_size = buy_qty + get_qty
    complete_bundles = len(unit_prices) // bundle_size
    if complete_bundles <= 0:
        return 0.0

    free_items = complete_bundles * get_qty
    discount = sum(unit_prices[:free_items]) * (get_percent / 100)
    cap = _discount_cap(voucher)
    if cap is not None:
        discount = min(discount, cap)
    return round_money(max(0, discount))


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

    eligibility_rules_dict = getattr(voucher, "eligibility_rules", None)
    if eligibility_rules_dict:
        from app.services.eligibility_service import parse_eligibility_rules, compute_user_eligibility_stats, evaluate_eligibility
        rules = parse_eligibility_rules(eligibility_rules_dict)
        if rules:
            stats = compute_user_eligibility_stats(db, user.id)
            ok, reason = evaluate_eligibility(rules, stats, now=now)
            if not ok:
                _raise_voucher_error(reason or "This voucher is not available for your account.")

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

    discount_amount = discount_for_voucher(
        voucher,
        eligible_subtotal_amount,
        shipping_amount,
        items=items,
        product_meta_map=product_meta_map,
        line_prices=line_prices,
    )
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


def resolve_voucher_owner_type(
    *,
    owner_type: str | None = None,
    created_by_is_vendor: bool = False,
) -> str:
    if owner_type in SUPPORTED_VOUCHER_OWNER_TYPES:
        return owner_type
    return "vendor" if created_by_is_vendor else "platform"


def validate_voucher_configuration(
    *,
    scope: str,
    availability: str,
    discount_type: str,
    discount_value: float,
    starts_at: datetime | None,
    ends_at: datetime | None,
    usage_limit: int | None,
    per_user_limit: int | None,
    store_id: str | None,
    promotion_type: str = "coupon",
    owner_type: str = "platform",
    bogo_buy_quantity: int | None = None,
    bogo_get_quantity: int | None = None,
    bogo_get_discount_percent: float | None = None,
    category_slugs: list[str] | None = None,
    product_ids: list[str] | None = None,
    eligible_store_ids: list[str] | None = None,
    excluded_category_slugs: list[str] | None = None,
) -> None:
    if scope not in SUPPORTED_VOUCHER_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher scope.",
        )
    if owner_type not in SUPPORTED_VOUCHER_OWNER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher owner type.",
        )
    if availability not in SUPPORTED_VOUCHER_AVAILABILITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher availability mode.",
        )
    if promotion_type not in PROMOTION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported promotion type.",
        )
    if discount_type not in SUPPORTED_VOUCHER_DISCOUNT_TYPES and promotion_type != "bogo":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher discount type.",
        )
    if discount_type == "percent" and discount_value > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Percent vouchers cannot exceed 100%.",
        )
    if owner_type == "vendor":
        if scope != "store" or not store_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vendor vouchers must be scoped to the vendor's own store.",
            )
        if eligible_store_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vendor vouchers cannot target other stores.",
            )
    if scope == "store" and not store_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store promotions must be attached to a store.",
        )
    if owner_type == "vendor" and discount_type == "free_shipping":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store promotions do not support free shipping yet.",
        )
    if scope == "store" and discount_type == "free_shipping" and owner_type == "vendor":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store promotions do not support free shipping yet.",
        )
    if scope == "category" and not category_slugs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category promotions require at least one category slug.",
        )
    if scope == "product" and not product_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product promotions require at least one product id.",
        )
    if promotion_type == "bogo":
        if not bogo_buy_quantity or not bogo_get_quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="BOGO promotions require buy and get quantities.",
            )
        if bogo_get_discount_percent is not None and not (0 <= bogo_get_discount_percent <= 100):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="BOGO get discount percent must be between 0 and 100.",
            )
    if starts_at and ends_at and ends_at < starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Voucher end date must be after the start date.",
        )
    if usage_limit is not None and per_user_limit is not None and per_user_limit > usage_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Per-user limit cannot be higher than the total usage limit.",
        )
    _ = excluded_category_slugs  # validated as optional list by schema layer

