"""Server-authoritative pricing for service bookings.

Every figure a customer is charged is computed here from rows read out of the
database. The client sends *choices* — which package, which add-ons, how many
hours — never amounts. A request that supplies a price is not trusted and not
consulted; this mirrors how product checkout already refuses client totals.

Commission reuses `settings.vendor_commission_rate`, the same rate orders are
settled at, so a provider's cut does not depend on which side of the
marketplace they sell through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.models.service import Service, ServiceAddon, ServicePackage
from app.services.finance_math import round_money


class PricingError(ValueError):
    """A booking request that cannot be priced as asked."""


@dataclass(frozen=True)
class AddonSelection:
    """A customer's choice of one add-on, before it is priced."""

    addon_id: str
    quantity: int = 1


@dataclass
class PricedAddon:
    addon_id: str
    title: str
    unit_price: float
    quantity: int
    duration_minutes: int
    line_total: float


@dataclass
class PriceQuote:
    """The full, server-computed cost of a booking."""

    currency: str
    base_amount: float
    addons_amount: float
    total_amount: float
    deposit_amount: float
    due_now: float
    commission_rate: float
    commission_amount: float
    provider_net_amount: float
    #: Total appointment length including add-ons and the provider's buffer.
    duration_minutes: int
    addons: list[PricedAddon] = field(default_factory=list)
    #: Frozen onto the booking so a later price change cannot restate history.
    breakdown: dict = field(default_factory=dict)

    @property
    def requires_payment_to_confirm(self) -> bool:
        return self.due_now > 0


def _package_for(service: Service, package_id: str | None) -> ServicePackage | None:
    if service.pricing_model != "package":
        if package_id:
            raise PricingError("This service is not sold as packages.")
        return None

    if not package_id:
        raise PricingError("Choose a package to continue.")

    for package in service.packages:
        if str(package.id) == str(package_id):
            if not package.is_active:
                raise PricingError("That package is no longer offered.")
            return package
    raise PricingError("That package does not belong to this service.")


def _base_amount(
    service: Service,
    package: ServicePackage | None,
    hours: float | None,
) -> tuple[float, int | None]:
    """Returns (amount, duration override from the package if any)."""

    model = service.pricing_model

    if model == "package":
        assert package is not None  # guaranteed by _package_for
        return round_money(package.price), package.duration_minutes

    if model == "hourly":
        if hours is None or hours <= 0:
            raise PricingError("Tell us how many hours you need.")
        # Half-hour granularity: finer increments invite disputes over
        # minutes, coarser over-bills a 90-minute job as two hours.
        if round(hours * 2) != hours * 2:
            raise PricingError("Hours must be given in half-hour steps.")
        duration = int(round(hours * 60))
        return round_money(service.base_price * hours), duration

    if model in ("quote", "consultation"):
        # There is no price yet. A quote booking is priced when the provider
        # answers; a consultation is free by definition.
        return 0.0, None

    # 'fixed' and 'starting_at' both begin from the listed price. The
    # difference is a promise to the customer, not a different sum: a
    # starting price may be revised upward by a quote before confirmation.
    return round_money(service.base_price), None


def _price_addons(
    service: Service, selections: list[AddonSelection]
) -> tuple[list[PricedAddon], float, int]:
    if not selections:
        return [], 0.0, 0

    by_id = {str(addon.id): addon for addon in service.addons}
    seen: set[str] = set()
    priced: list[PricedAddon] = []
    total = 0.0
    extra_minutes = 0

    for selection in selections:
        key = str(selection.addon_id)
        if key in seen:
            raise PricingError("The same extra was selected twice.")
        seen.add(key)

        addon: ServiceAddon | None = by_id.get(key)
        if addon is None:
            raise PricingError("That extra does not belong to this service.")
        if not addon.is_active:
            raise PricingError(f"“{addon.title}” is no longer available.")

        quantity = int(selection.quantity or 1)
        if quantity < 1:
            raise PricingError("Quantity must be at least one.")
        if quantity > addon.max_quantity:
            raise PricingError(
                f"“{addon.title}” is limited to {addon.max_quantity} per booking."
            )

        line_total = round_money(addon.price * quantity)
        total = round_money(total + line_total)
        extra_minutes += addon.duration_minutes * quantity
        priced.append(
            PricedAddon(
                addon_id=key,
                title=addon.title,
                unit_price=round_money(addon.price),
                quantity=quantity,
                duration_minutes=addon.duration_minutes * quantity,
                line_total=line_total,
            )
        )

    return priced, total, extra_minutes


def _due_now(service: Service, total: float) -> float:
    """What the customer pays to hold the booking: all of it, or nothing.

    Deposits were removed deliberately. Odos takes the full amount and holds
    it until the work is confirmed, so the customer's protection comes from
    the escrow rather than from withholding part of the price -- which also
    removes the balance nobody wanted to chase and the second payment row it
    created per booking.
    """

    if service.payment_timing == "free" or total <= 0:
        return 0.0
    return total


def quote_booking(
    service: Service,
    *,
    package_id: str | None = None,
    addons: list[AddonSelection] | None = None,
    hours: float | None = None,
    commission_rate: float | None = None,
) -> PriceQuote:
    """Price a booking from the listing as it stands in the database."""

    if service.status != "active":
        raise PricingError("This service is not currently bookable.")

    package = _package_for(service, package_id)
    base_amount, package_duration = _base_amount(service, package, hours)
    priced_addons, addons_amount, addon_minutes = _price_addons(service, addons or [])

    total = round_money(base_amount + addons_amount)
    due_now = _due_now(service, total)

    effective_rate = (
        float(settings.vendor_commission_rate)
        if commission_rate is None
        else float(commission_rate)
    )
    commission_amount = round_money(total * effective_rate)
    provider_net = round_money(total - commission_amount)

    # Package duration wins over the listing default — a Premium shoot is
    # longer than a Basic one — then add-ons extend it, then the provider's
    # buffer closes the gap before the next customer.
    core_minutes = package_duration or service.duration_minutes or 0
    duration_minutes = core_minutes + addon_minutes
    if duration_minutes:
        duration_minutes += service.buffer_minutes

    return PriceQuote(
        currency=service.currency,
        base_amount=base_amount,
        addons_amount=addons_amount,
        total_amount=total,
        deposit_amount=0.0,
        due_now=due_now,
        commission_rate=effective_rate,
        commission_amount=commission_amount,
        provider_net_amount=provider_net,
        duration_minutes=duration_minutes,
        addons=priced_addons,
        breakdown={
            "pricing_model": service.pricing_model,
            "payment_timing": service.payment_timing,
            "base_amount": base_amount,
            "package": (
                {"id": str(package.id), "title": package.title, "price": round_money(package.price)}
                if package
                else None
            ),
            "hours": hours,
            "addons": [
                {
                    "id": item.addon_id,
                    "title": item.title,
                    "unit_price": item.unit_price,
                    "quantity": item.quantity,
                    "line_total": item.line_total,
                }
                for item in priced_addons
            ],
            "addons_amount": addons_amount,
            "total_amount": total,
            "due_now": due_now,
            "commission_rate": effective_rate,
            "commission_amount": commission_amount,
            "provider_net_amount": provider_net,
        },
    )
