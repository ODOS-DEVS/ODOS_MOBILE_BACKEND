"""Slot arithmetic: overlap, lead time, and the booking horizon.

The database owns the double-booking guarantee; what is tested here is that
the offered list is honest, so a customer is rarely sent at a slot the
constraint will reject. Overlap is unit-tested directly because its
half-open semantics must match the '[)' range in the exclusion constraint —
if these two ever disagree, the app either hides bookable time or offers
time it cannot sell.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.service_availability import SLOT_STEP_MINUTES, Slot


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 1, hour, minute, tzinfo=timezone.utc)


class TestOverlapSemantics:
    def test_back_to_back_slots_do_not_collide(self):
        """09:00-10:00 and 10:00-11:00 are both sellable."""
        slot = Slot(at(9), at(10))
        assert not slot.overlaps(at(10), at(11))

    def test_a_slot_ending_as_another_starts_does_not_collide(self):
        slot = Slot(at(10), at(11))
        assert not slot.overlaps(at(9), at(10))

    def test_partial_overlap_collides(self):
        slot = Slot(at(9), at(10))
        assert slot.overlaps(at(9, 30), at(10, 30))

    def test_containment_collides_both_ways(self):
        outer = Slot(at(9), at(12))
        assert outer.overlaps(at(10), at(11))
        inner = Slot(at(10), at(11))
        assert inner.overlaps(at(9), at(12))

    def test_identical_slots_collide(self):
        slot = Slot(at(9), at(10))
        assert slot.overlaps(at(9), at(10))

    def test_distant_slots_do_not_collide(self):
        slot = Slot(at(9), at(10))
        assert not slot.overlaps(at(14), at(15))

    @pytest.mark.parametrize(
        "other_start,other_end,expected",
        [
            (at(8), at(9), False),      # ends exactly at start
            (at(8), at(9, 1), True),    # one minute in
            (at(9, 59), at(11), True),  # one minute before end
            (at(10), at(11), False),    # starts exactly at end
        ],
    )
    def test_boundary_minutes(self, other_start, other_end, expected):
        assert Slot(at(9), at(10)).overlaps(other_start, other_end) is expected


class TestSlotGridAssumptions:
    def test_step_divides_an_hour(self):
        """A grid that does not divide an hour drifts off the clock."""
        assert 60 % SLOT_STEP_MINUTES == 0

    def test_a_long_booking_covers_several_grid_steps(self):
        """An eight-hour wedding must block the whole day, not one step."""
        slot = Slot(at(9), at(17))
        step = timedelta(minutes=SLOT_STEP_MINUTES)
        cursor = at(9)
        blocked = 0
        while cursor < at(17):
            if slot.overlaps(cursor, cursor + step):
                blocked += 1
            cursor += step
        assert blocked == (8 * 60) // SLOT_STEP_MINUTES
