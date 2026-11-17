"""Scheduled housekeeping for the services marketplace.

Two sweeps that exist so the marketplace does not silently seize up:

  * Unpaid bookings past their payment window are expired, which returns the
    slot to the calendar. Without this, opening a checkout and walking away
    would hold a Saturday morning forever.
  * Quotes nobody acted on are closed, so a provider's inbox is not a
    graveyard of jobs priced months ago.

Separate from the escrow sweep: that one moves money and deserves to fail
loudly on its own. These are tidying, and one failing must not stop the other.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def process_services_maintenance() -> None:
    """Entry point for the scheduler. Owns its session."""

    from app.core.database import SessionLocal
    from app.services.service_booking_service import expire_stale_bookings
    from app.services.service_quotation_service import expire_stale_quotations

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        try:
            expired_bookings = expire_stale_bookings(db, now=now)
            db.commit()
            if expired_bookings:
                logger.info("Expired %s unpaid bookings", expired_bookings)
        except Exception:
            db.rollback()
            logger.exception("Failed expiring stale bookings")

        try:
            expired_quotes = expire_stale_quotations(db, now=now)
            db.commit()
            if expired_quotes:
                logger.info("Expired %s stale quotations", expired_quotes)
        except Exception:
            db.rollback()
            logger.exception("Failed expiring stale quotations")
    finally:
        db.close()
