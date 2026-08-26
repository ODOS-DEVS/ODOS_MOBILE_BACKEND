"""Celery tasks — thin wrappers around the existing periodic job functions.

Deliberately thin. Each task does nothing but call the service function that already exists and
is already exercised in production by the in-process loops. Putting logic here would mean the
Celery path and the asyncio path could drift, and only one of them is running at a time — so a
divergence would surface only after switching modes, which is the worst time to find it.

The service functions are imported **inside** each task rather than at module scope. Importing
them at the top pulls in the controllers, which pull in most of the application; doing that at
import time makes worker startup slow and makes an unrelated import error look like a Celery
configuration problem.

Errors are logged and swallowed rather than raised. These are periodic maintenance jobs: a
failed run should be visible and then retried on the next tick, not retried immediately in a
tight loop against whatever is broken. This matches what the asyncio loops already do.
"""

from __future__ import annotations

import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(job_name: str, run) -> str:
    """Execute one job, logging rather than propagating a failure.

    Returns a short status string, which shows up in the Celery result and in `celery events` —
    enough to tell "ran cleanly" from "ran and blew up" without opening the logs.
    """
    try:
        run()
    except Exception:
        logger.exception("Scheduled job failed", extra={"job": job_name})
        return f"{job_name}: failed"
    return f"{job_name}: ok"


@celery_app.task(name="app.tasks.vendor_order_reminders")
def vendor_order_reminders() -> str:
    """Nudge vendors about orders they have not yet acted on."""
    from app.services.vendor_order_reminder_service import process_vendor_order_reminders

    return _run("vendor_order_reminders", process_vendor_order_reminders)


@celery_app.task(name="app.tasks.delivery_sla_monitor")
def delivery_sla_monitor() -> str:
    """Flag late deliveries and credit customer goodwill on an SLA breach."""
    from app.services.delivery_ops_monitor_service import process_delivery_sla_alerts

    return _run("delivery_sla_monitor", process_delivery_sla_alerts)


@celery_app.task(name="app.tasks.promo_expiry_reminders")
def promo_expiry_reminders() -> str:
    """Remind shoppers and vendors before a saved voucher expires."""
    from app.services.promo_reminder_service import process_promo_expiry_reminders

    return _run("promo_expiry_reminders", process_promo_expiry_reminders)


@celery_app.task(name="app.tasks.payment_reconciliation")
def payment_reconciliation() -> str:
    """Re-verify payments stuck `pending` directly against Paystack.

    The most consequential of the five: it is what rescues an order whose webhook never
    arrived. It re-reads state and acts only on transactions still pending, so a redelivered
    message re-does nothing.
    """
    from app.services.payment_reconciliation_service import (
        process_stuck_payment_reconciliation,
    )

    return _run("payment_reconciliation", process_stuck_payment_reconciliation)


@celery_app.task(name="app.tasks.delivery_auto_release")
def delivery_auto_release() -> str:
    """Remind at 36h, then auto-confirm delivery and settle the vendor at 48h."""
    from app.services.delivery_auto_release_service import process_delivery_auto_release

    return _run("delivery_auto_release", process_delivery_auto_release)
