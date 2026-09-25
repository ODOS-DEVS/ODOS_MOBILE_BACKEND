"""Delivery pricing when an order is several packages.

The rule this module implements, in one sentence: **the delivery fee follows
the rider.**

Under today's fulfilment model the shop dispatches its own rider and pays them
out of pocket, so the shop sets the price and the shop receives it. A single
platform-wide fee was a number set by the one party not buying the fuel: it
overcharged the shop delivering two streets away and quietly underpaid the one
crossing Accra, while ODOS kept the whole fee for a ride it never made. That is
the arrangement this replaces.

Three consequences fall out of it, and they are why this file exists:

* **A fee per package, not per order.** Three shops means three riders and
  three real journeys. Charging once and letting two vendors absorb the rest
  is not a discount, it is a bill moved into someone else's pocket.

* **No "additional package" discount.** It is tempting to make the second and
  third package cheaper so mixed carts sting less -- but every cedi of that
  discount would come out of a vendor who is still making the whole journey.
  Volume discounts are only honest when the discounter owns the cost. When
  ODOS runs its own riders and can actually batch two drops into one trip, the
  saving becomes real and this is where it will be expressed.

* **The free-delivery threshold is per package**, measured against that
  package's own subtotal. A GH₵400 dress ships free even when it shares a cart
  with a GH₵30 phone case, and the shop that waived the fee is the shop that
  chose to.

Every price here is a fallback chain: the store's own number, or the platform
default when the store has never set one. A store that never opens the setting
prices exactly as it did before this module existed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.models.catalog import Store
from app.services.delivery_service import (
    DEFAULT_DELIVERY_CONFIG,
    DeliveryConfig,
    DeliveryMethodId,
    DeliveryOption,
    is_same_day_eligible_location,
    is_same_day_order_window_open,
)

#: A vendor cannot price delivery above this. Not an economic judgement -- a
#: guard against a typo turning GH₵15 into GH₵1500 at checkout, and against a
#: shop using an absurd delivery fee to dodge commission on the goods.
MAX_VENDOR_DELIVERY_FEE = 200.0

#: Likewise for the free-delivery threshold: a shop advertising "free over
#: GH₵50,000" is advertising nothing.
MAX_FREE_DELIVERY_THRESHOLD = 5000.0


@dataclass(frozen=True)
class VendorDeliveryPricing:
    """What one shop charges to deliver, after platform fallbacks."""

    economy_fee: float
    express_fee: float
    same_day_fee: float
    free_threshold: float
    express_enabled: bool
    same_day_enabled: bool
    #: True when this shop set at least one of its own numbers. Used to decide
    #: whether the customer is looking at the shop's price or the platform's.
    is_custom: bool

    def fee_for(self, method: str) -> float:
        if method == "express":
            return self.express_fee
        if method == "same_day":
            return self.same_day_fee
        return self.economy_fee

    def supports(self, method: str) -> bool:
        if method == "express":
            return self.express_enabled
        if method == "same_day":
            return self.same_day_enabled
        return True


def vendor_delivery_pricing(
    store: Store | None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> VendorDeliveryPricing:
    """This store's delivery prices, falling back to the platform's.

    `store` is allowed to be None: an item whose store row has been removed
    still has to be priced, and the platform default is the only defensible
    answer left.
    """
    if store is None:
        return VendorDeliveryPricing(
            economy_fee=config.economy_fee,
            express_fee=config.express_fee,
            same_day_fee=config.same_day_fee,
            free_threshold=config.free_shipping_threshold,
            express_enabled=config.express_enabled,
            same_day_enabled=config.same_day_enabled,
            is_custom=False,
        )

    custom_fields = (
        store.delivery_fee_economy,
        store.delivery_fee_express,
        store.delivery_fee_same_day,
        store.free_delivery_threshold,
    )
    return VendorDeliveryPricing(
        economy_fee=_fallback(store.delivery_fee_economy, config.economy_fee),
        express_fee=_fallback(store.delivery_fee_express, config.express_fee),
        same_day_fee=_fallback(store.delivery_fee_same_day, config.same_day_fee),
        free_threshold=_fallback(
            store.free_delivery_threshold, config.free_shipping_threshold
        ),
        # A store can switch a method off, but cannot switch one on that the
        # platform has disabled -- same-day is an ODOS-wide capability
        # (cut-off times, covered regions), not a per-shop one.
        express_enabled=config.express_enabled and bool(store.express_delivery_enabled),
        same_day_enabled=config.same_day_enabled and bool(store.same_day_delivery_enabled),
        is_custom=any(value is not None for value in custom_fields),
    )


def _fallback(value: float | None, default: float) -> float:
    """Zero is a real price ("I deliver free"), so only None falls back."""
    return float(default) if value is None else float(value)


def store_delivery_badge(
    store: Store | None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> str | None:
    """The delivery badge for a store card, or None when there is nothing
    worth shouting about.

    Only two states earn a badge. "Free delivery" (this shop never charges) is
    the one that changes a shopper's mind; "Free over GH₵X" is the softer
    nudge. A shop charging an ordinary fee gets no badge rather than a badge
    saying it charges money.
    """
    pricing = vendor_delivery_pricing(store, config)
    if pricing.free_threshold <= 0:
        return "Free delivery"
    if pricing.economy_fee <= 0:
        return "Free delivery"
    return f"Free over GH₵{pricing.free_threshold:.0f}"


@dataclass(frozen=True)
class PackageGroup:
    """One vendor's slice of a cart, as the caller knows it before pricing."""

    vendor_user_id: uuid.UUID | None
    store_id: str | None
    store_name: str | None
    store: Store | None
    items_subtotal: float


@dataclass(frozen=True)
class PackageQuote:
    """One vendor's slice of a cart, priced."""

    vendor_user_id: uuid.UUID | None
    store_id: str | None
    store_name: str | None
    items_subtotal: float
    delivery_fee: float
    #: Zero because the basket cleared the shop's threshold, as opposed to
    #: zero because the shop prices delivery at nothing. The receipt reads
    #: differently in each case.
    fee_waived: bool
    free_threshold: float
    #: How much more this customer would have to spend with *this shop* to
    #: stop paying for its delivery. None once there is nothing to gain.
    amount_to_free_delivery: float | None


def quote_package(
    group: PackageGroup,
    method: DeliveryMethodId,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> PackageQuote:
    pricing = vendor_delivery_pricing(group.store, config)
    subtotal = round(float(group.items_subtotal), 2)
    threshold = pricing.free_threshold
    base_fee = round(pricing.fee_for(method), 2)

    # A threshold of zero is a shop saying "I always deliver free", not a
    # threshold nobody can reach. Kept distinct from `waived`, which means the
    # basket grew past a real threshold -- the receipt reads "Free delivery"
    # in the first case and "Free delivery (over GH₵299)" in the second.
    always_free = threshold <= 0 or base_fee <= 0
    waived = not always_free and subtotal >= threshold
    fee = 0.0 if (always_free or waived) else base_fee

    remaining = None if fee <= 0 else round(max(threshold - subtotal, 0.0), 2) or None

    return PackageQuote(
        vendor_user_id=group.vendor_user_id,
        store_id=group.store_id,
        store_name=group.store_name,
        items_subtotal=subtotal,
        delivery_fee=round(fee, 2),
        fee_waived=waived,
        free_threshold=threshold,
        amount_to_free_delivery=remaining,
    )


def quote_packages(
    groups: list[PackageGroup],
    method: DeliveryMethodId,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> list[PackageQuote]:
    return [quote_package(group, method, config) for group in groups]


def packages_shipping_total(quotes: list[PackageQuote]) -> float:
    return round(sum(quote.delivery_fee for quote in quotes), 2)


def _method_supported_by_all(groups: list[PackageGroup], method: str, config) -> bool:
    """One delivery method is chosen for the whole order.

    Per-package methods would be expressible -- the packages travel
    independently -- but it would mean a checkout screen asking the customer to
    make a separate speed choice per shop, for a gain most carts never use. So
    a method a single shop in the cart cannot do is offered to none of it,
    which is at least honest about what will actually happen.
    """
    return all(vendor_delivery_pricing(g.store, config).supports(method) for g in groups)


def build_package_delivery_options(
    *,
    groups: list[PackageGroup],
    region: str | None,
    city: str | None = None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
    now: datetime | None = None,
) -> list[DeliveryOption]:
    """The delivery choices for a cart, priced as the sum of its packages.

    Same shape as `delivery_service.build_delivery_options` -- deliberately, so
    every existing caller and the checkout UI keep working -- but each amount
    is now the total across the cart's packages rather than one flat fee, and
    the subtitle says how that total was reached when more than one shop is
    involved.
    """
    package_count = len(groups)
    same_day_location_ok = is_same_day_eligible_location(
        region=region, city=city, config=config
    )
    same_day_window_ok = is_same_day_order_window_open(config, now)
    cutoff_label = f"{config.same_day_cutoff_hour}:00"

    def total_for(method: DeliveryMethodId) -> tuple[float, int]:
        quotes = quote_packages(groups, method, config)
        return packages_shipping_total(quotes), sum(1 for q in quotes if q.delivery_fee <= 0)

    def multi_store_note(amount: float, free_count: int) -> str | None:
        if package_count <= 1:
            return None
        if amount <= 0:
            return f"Free delivery from all {package_count} shops"
        if free_count:
            return (
                f"{package_count} shops · {free_count} delivering free"
            )
        return f"{package_count} separate deliveries, one per shop"

    options: list[DeliveryOption] = []

    if config.economy_enabled:
        amount, free_count = total_for("economy")
        note = multi_store_note(amount, free_count)
        if note:
            subtitle = note
        elif amount <= 0:
            subtitle = "Free delivery on this order"
        else:
            subtitle = "Delivered by the shop you bought from"
        options.append(
            DeliveryOption(
                id="economy",
                title=config.economy_title,
                subtitle=subtitle,
                eta=config.economy_eta,
                amount=amount,
                badge="Free" if amount <= 0 else None,
                available=True,
            )
        )

    if config.express_enabled:
        supported = _method_supported_by_all(groups, "express", config)
        amount, free_count = total_for("express")
        options.append(
            DeliveryOption(
                id="express",
                title=config.express_title,
                subtitle=(
                    multi_store_note(amount, free_count) or "Priority dispatch from the shop"
                )
                if supported
                else "One of the shops in your cart doesn't offer express",
                eta=config.express_eta if supported else "Unavailable",
                amount=amount,
                badge="Free" if supported and amount <= 0 else None,
                available=supported,
                unavailable_reason=(
                    None if supported else "Remove that shop's items, or choose standard delivery"
                ),
            )
        )

    if config.same_day_enabled:
        supported = _method_supported_by_all(groups, "same_day", config)
        available = supported and same_day_location_ok and same_day_window_ok
        amount, free_count = total_for("same_day")

        if not supported:
            reason = "One of the shops in your cart doesn't offer same-day"
            subtitle = "Choose express or standard delivery instead"
            eta = "Unavailable"
        elif not same_day_location_ok:
            reason = "Select a Greater Accra address to unlock same-day"
            subtitle = "Available for Greater Accra addresses"
            eta = "Greater Accra only"
        elif not same_day_window_ok:
            current = _in_accra(now)
            reason = (
                "Same-day resumes Monday before 2:00 PM"
                if current.weekday() == 6
                else f"Same-day orders close at {cutoff_label} (Mon–Sat)"
            )
            subtitle = f"Order before {cutoff_label} (Mon–Sat) for evening drop-off"
            eta = "Opens Monday" if current.weekday() == 6 else "Closed for today"
        else:
            reason = None
            subtitle = (
                multi_store_note(amount, free_count)
                or f"Order before {cutoff_label} for evening drop-off"
            )
            eta = config.same_day_eta

        options.append(
            DeliveryOption(
                id="same_day",
                title=config.same_day_title,
                subtitle=subtitle,
                eta=eta,
                amount=amount,
                badge="Live" if available else None,
                available=available,
                unavailable_reason=reason,
            )
        )

    if not options:
        amount, free_count = total_for("economy")
        options.append(
            DeliveryOption(
                id="economy",
                title=config.economy_title,
                subtitle=multi_store_note(amount, free_count) or "Delivered by the shop you bought from",
                eta=config.economy_eta,
                amount=amount,
                badge="Free" if amount <= 0 else None,
                available=True,
            )
        )

    return options


def _in_accra(now: datetime | None) -> datetime:
    from app.services.delivery_service import ACCRA_TZ

    current = now or datetime.now(ACCRA_TZ)
    if current.tzinfo is None:
        return current.replace(tzinfo=ACCRA_TZ)
    return current.astimezone(ACCRA_TZ)


def annotate_store_delivery(
    stores: list[Store] | Store | None,
    config: DeliveryConfig = DEFAULT_DELIVERY_CONFIG,
) -> None:
    """Attach `delivery_badge` and `delivery_fee_from` to store rows for
    serialization.

    Computed here rather than as model properties because the fallback half of
    every price is the *platform* config, which lives in the database and is
    admin-editable. A property would have to guess it from module defaults and
    would quietly go stale the first time an admin changed the standard fee.

    Assigned onto the instance so `StoreRead.model_validate` picks both up with
    no bespoke serializer, which keeps every existing store endpoint —
    list, detail, cached or not — working unchanged.
    """
    if stores is None:
        return
    rows = stores if isinstance(stores, list) else [stores]
    for store in rows:
        pricing = vendor_delivery_pricing(store, config)
        store.delivery_badge = store_delivery_badge(store, config)
        store.delivery_fee_from = round(pricing.economy_fee, 2)
