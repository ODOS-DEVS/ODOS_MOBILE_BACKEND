"""Which times a service can actually be booked.

Three layers decide a slot, in order:

  1. The weekly pattern (`ServiceAvailabilityRule`) — the provider's normal week.
  2. Date overrides (`ServiceAvailabilityException`) — a holiday closes a day
     outright; a one-off Saturday opens one the weekly pattern does not cover.
  3. Bookings already holding time.

This module *offers* slots. It does not reserve them, and nothing here is the
safeguard against two customers taking the same one — that is the database's
`ix_bookings_no_overlap` exclusion constraint. A read like this is always
racing something: the honest job is to show a good list and let the write
fail cleanly if the world moved underneath it.

All arithmetic is done in the provider's local timezone, because "Tuesday
09:00" is a statement about where the provider is standing, then converted to
UTC for storage and comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.service import (
    BLOCKING_BOOKING_STATUSES,
    Booking,
    Service,
    ServiceAvailabilityException,
    ServiceAvailabilityRule,
)

#: Spacing of the offered grid. Thirty minutes matches how appointment trades
#: actually quote time; a finer grid produces a wall of near-identical
#: choices without making anything more bookable.
SLOT_STEP_MINUTES = 30


class AvailabilityError(ValueError):
    """The requested time cannot be offered."""


@dataclass(frozen=True)
class Slot:
    start_at: datetime
    end_at: datetime

    def overlaps(self, other_start: datetime, other_end: datetime) -> bool:
        # Half-open [start, end): a booking ending at 10:00 and one starting
        # at 10:00 do not collide. Matches the '[)' range in the database
        # constraint, so this module and Postgres agree about touching slots.
        return self.start_at < other_end and other_start < self.end_at


def _tz(service: Service) -> ZoneInfo:
    # Services carry no timezone of their own yet; bookings default to
    # Africa/Accra and Odos operates in Ghana. Kept behind this helper so a
    # per-store timezone becomes a one-line change rather than a sweep.
    return ZoneInfo("Africa/Accra")


def _windows_for_day(
    db: Session, service: Service, day: date, tz: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    """The open periods on one calendar day, after overrides."""

    exception = db.scalar(
        select(ServiceAvailabilityException)
        .where(
            ServiceAvailabilityException.store_id == service.store_id,
            ServiceAvailabilityException.exception_date == day,
            or_(
                ServiceAvailabilityException.service_id == service.id,
                ServiceAvailabilityException.service_id.is_(None),
            ),
        )
        # A service-specific override beats the store-wide one for the same
        # date: the provider closed the shop but is still taking this job.
        .order_by(ServiceAvailabilityException.service_id.nulls_last())
    )

    if exception is not None:
        if not exception.is_available:
            return []
        if exception.start_time and exception.end_time:
            return [
                (
                    datetime.combine(day, exception.start_time, tzinfo=tz),
                    datetime.combine(day, exception.end_time, tzinfo=tz),
                )
            ]
        # Open, but no hours given: fall through to the weekly pattern.

    rules = db.scalars(
        select(ServiceAvailabilityRule).where(
            ServiceAvailabilityRule.service_id == service.id,
            ServiceAvailabilityRule.weekday == day.weekday(),
            ServiceAvailabilityRule.is_active.is_(True),
        )
    ).all()

    return [
        (
            datetime.combine(day, rule.start_time, tzinfo=tz),
            datetime.combine(day, rule.end_time, tzinfo=tz),
        )
        for rule in rules
    ]


def _busy_periods(
    db: Session, service: Service, window_start: datetime, window_end: datetime
) -> list[tuple[datetime, datetime]]:
    """Time already held by bookings that block the calendar."""

    rows = db.execute(
        select(Booking.scheduled_start_at, Booking.scheduled_end_at).where(
            Booking.service_id == service.id,
            Booking.status.in_(BLOCKING_BOOKING_STATUSES),
            Booking.scheduled_start_at.is_not(None),
            Booking.scheduled_end_at.is_not(None),
            Booking.scheduled_start_at < window_end,
            Booking.scheduled_end_at > window_start,
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def available_slots(
    db: Session,
    service: Service,
    *,
    day: date,
    duration_minutes: int,
    now: datetime | None = None,
) -> list[Slot]:
    """Bookable start times on `day` for an appointment of `duration_minutes`.

    `duration_minutes` comes from the pricing engine, not the listing: a
    Premium package with two add-on hours occupies far more of the day than
    the service's default, and offering slots that cannot fit it would hand
    the customer a booking the database then rejects.
    """

    if duration_minutes <= 0:
        raise AvailabilityError("This service has no appointment length set.")
    if service.status != "active":
        return []

    tz = _tz(service)
    moment = (now or datetime.now(tz)).astimezone(tz)

    # Booking windows: too soon, and the provider cannot prepare; too far
    # ahead, and they are committing to a diary they have not planned.
    earliest = moment + timedelta(hours=service.lead_time_hours)
    latest = moment + timedelta(days=service.max_advance_days)

    windows = _windows_for_day(db, service, day, tz)
    if not windows:
        return []

    day_start = datetime.combine(day, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    busy = _busy_periods(db, service, day_start, day_end)

    step = timedelta(minutes=SLOT_STEP_MINUTES)
    length = timedelta(minutes=duration_minutes)
    slots: list[Slot] = []

    for window_start, window_end in windows:
        cursor = window_start
        while cursor + length <= window_end:
            slot = Slot(cursor, cursor + length)
            if slot.start_at < earliest or slot.start_at > latest:
                cursor += step
                continue
            if any(slot.overlaps(busy_start, busy_end) for busy_start, busy_end in busy):
                cursor += step
                continue
            slots.append(slot)
            cursor += step

    return slots


def assert_slot_bookable(
    db: Session,
    service: Service,
    *,
    start_at: datetime,
    duration_minutes: int,
    now: datetime | None = None,
) -> datetime:
    """Validate a requested start and return its end, or raise.

    Called before the insert so the customer gets a readable reason rather
    than a constraint violation. The constraint still has the final say —
    this check and the write are not atomic, and cannot be.
    """

    if duration_minutes <= 0:
        raise AvailabilityError("This service has no appointment length set.")

    tz = _tz(service)
    start_local = start_at.astimezone(tz)
    end_local = start_local + timedelta(minutes=duration_minutes)
    moment = (now or datetime.now(tz)).astimezone(tz)

    if start_local < moment + timedelta(hours=service.lead_time_hours):
        raise AvailabilityError(
            f"This service needs at least {service.lead_time_hours} hours' notice."
        )
    if start_local > moment + timedelta(days=service.max_advance_days):
        raise AvailabilityError(
            f"Bookings open {service.max_advance_days} days ahead."
        )

    windows = _windows_for_day(db, service, start_local.date(), tz)
    if not any(
        window_start <= start_local and end_local <= window_end
        for window_start, window_end in windows
    ):
        raise AvailabilityError("The provider is not available then.")

    candidate = Slot(start_local, end_local)
    for busy_start, busy_end in _busy_periods(db, service, start_local, end_local):
        if candidate.overlaps(busy_start, busy_end):
            raise AvailabilityError("That time has just been taken.")

    return end_local
