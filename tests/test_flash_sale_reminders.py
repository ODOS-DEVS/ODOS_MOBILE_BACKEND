"""Flash sale notify-me: set/unset, and the start reminder firing exactly once.

DB-backed in the repo's standard pattern — these run wherever
TEST_DATABASE_URL points at a migrated schema, and skip elsewhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.controllers.catalog_controller import (
    list_my_flash_sale_reminders,
    set_flash_sale_reminder,
    unset_flash_sale_reminder,
)
from app.models import FlashSaleEvent, FlashSaleReminder
from app.services.promo_reminder_service import _process_flash_sale_start_reminders


@pytest.fixture
def shopper(make_user):
    return make_user()


def make_event(db, *, starts_in_minutes: int | None, ends_in_minutes: int = 120, active: bool = True):
    now = datetime.now(UTC)
    event = FlashSaleEvent(
        id=uuid.uuid4(),
        slug=f"flash-{uuid.uuid4().hex[:8]}",
        title="Test Flash",
        starts_at=(now + timedelta(minutes=starts_in_minutes)) if starts_in_minutes is not None else None,
        ends_at=now + timedelta(minutes=ends_in_minutes),
        is_active=active,
    )
    db.add(event)
    db.flush()
    return event


class TestSettingReminders:
    def test_setting_twice_is_one_reminder(self, db, shopper):
        event = make_event(db, starts_in_minutes=60)
        set_flash_sale_reminder(db, shopper, str(event.id))
        set_flash_sale_reminder(db, shopper, str(event.id))
        assert list_my_flash_sale_reminders(db, shopper) == [str(event.id)]

    def test_an_already_started_sale_cannot_be_subscribed(self, db, shopper):
        event = make_event(db, starts_in_minutes=-5)
        with pytest.raises(HTTPException) as err:
            set_flash_sale_reminder(db, shopper, str(event.id))
        assert err.value.status_code == 400

    def test_inactive_and_ended_events_read_as_not_found(self, db, shopper):
        inactive = make_event(db, starts_in_minutes=60, active=False)
        ended = make_event(db, starts_in_minutes=-120, ends_in_minutes=-30)
        for event in (inactive, ended):
            with pytest.raises(HTTPException) as err:
                set_flash_sale_reminder(db, shopper, str(event.id))
            assert err.value.status_code == 404

    def test_unset_removes_and_is_idempotent(self, db, shopper):
        event = make_event(db, starts_in_minutes=60)
        set_flash_sale_reminder(db, shopper, str(event.id))
        unset_flash_sale_reminder(db, shopper, str(event.id))
        unset_flash_sale_reminder(db, shopper, str(event.id))
        assert list_my_flash_sale_reminders(db, shopper) == []


class TestStartReminderJob:
    def _notifications(self, monkeypatch):
        sent = []
        import app.services.promo_reminder_service as svc

        monkeypatch.setattr(svc, "_notify", lambda db, user, **kw: sent.append((user.id, kw["kind"])))
        return sent

    def test_fires_once_inside_the_window_and_never_again(self, db, shopper, monkeypatch):
        sent = self._notifications(monkeypatch)
        event = make_event(db, starts_in_minutes=10)
        set_flash_sale_reminder(db, shopper, str(event.id))

        now = datetime.now(UTC)
        _process_flash_sale_start_reminders(db, now)
        assert sent == [(shopper.id, "flash_sale_starting")]

        # The window still matches on the next run; notified_at must gate it.
        _process_flash_sale_start_reminders(db, now + timedelta(minutes=5))
        assert len(sent) == 1

    def test_does_not_fire_outside_the_lead_window(self, db, shopper, monkeypatch):
        sent = self._notifications(monkeypatch)
        event = make_event(db, starts_in_minutes=60)
        set_flash_sale_reminder(db, shopper, str(event.id))
        _process_flash_sale_start_reminders(db, datetime.now(UTC))
        assert sent == []

    def test_does_not_fire_for_ended_or_deactivated_sales(self, db, shopper, monkeypatch):
        sent = self._notifications(monkeypatch)
        event = make_event(db, starts_in_minutes=5)
        set_flash_sale_reminder(db, shopper, str(event.id))

        event.is_active = False
        db.flush()
        _process_flash_sale_start_reminders(db, datetime.now(UTC))
        assert sent == []

        # A reminder on a sale that ended while the job was down stays silent too.
        event.is_active = True
        reminder = db.scalar(select(FlashSaleReminder).where(FlashSaleReminder.event_id == event.id))
        assert reminder is not None
        _process_flash_sale_start_reminders(db, datetime.now(UTC) + timedelta(minutes=300))
        assert sent == []
