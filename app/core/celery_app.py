"""Celery application and beat schedule.

--- Why this exists ---

The five periodic jobs currently run as `while True: await asyncio.sleep(...)` loops started in
`app/main.py`'s startup hook. That works for a single web process, but has two problems the
moment the API is containerised and scaled:

1. **Every replica runs every loop.** Two API containers means vendors get two reminder pushes,
   delivery SLA alerts fire twice, and — worst — `process_stuck_payment_reconciliation` runs
   concurrently against the same pending payments.
2. **The jobs die with the web process.** A slow job blocks nothing today only because it is
   dispatched to a thread; a crash in the event loop takes the schedule with it.

Celery beat schedules; Celery workers execute; the API just serves requests.

--- This does not change behaviour by default ---

`SCHEDULER_ENABLED` (default **true**) still runs the in-process loops, so a non-Docker
deployment — including the current Render one — behaves exactly as before. The Docker Compose
stack sets `SCHEDULER_ENABLED=false` on the API and runs the worker and beat services instead.

Exactly one of the two must be active. Both would double every job.

--- Task design ---

The five job functions take no arguments and open their own database session, so they wrap
directly with no refactoring. That is also why tasks here pass no ORM objects: a task argument
has to be JSON-serialisable and has to still be valid whenever the worker gets to it, which a
detached SQLAlchemy instance is not.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab  # noqa: F401  (available for future cron-style jobs)

from app.core.config import settings

# Intervals match the in-process loops exactly, so switching between the two modes does not
# change how often anything runs.
VENDOR_REMINDER_INTERVAL_SECONDS = 180
DELIVERY_SLA_MONITOR_INTERVAL_SECONDS = 120
PROMO_REMINDER_INTERVAL_SECONDS = 1800
PAYMENT_RECONCILIATION_INTERVAL_SECONDS = 300
DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS = 1800


def _broker_url() -> str:
    """Redis is both broker and result backend.

    Redis rather than RabbitMQ because Redis is already a dependency here (rate limiting and
    the catalog cache), and these jobs are low-volume periodic work — none of RabbitMQ's routing
    or durability guarantees would earn its operational cost.

    Falls back to the Compose service name so the container works without extra configuration;
    outside Docker, `REDIS_URL` must be set or the worker will not start.
    """
    return (settings.redis_url or "").strip() or "redis://redis:6379/0"


celery_app = Celery("odos", broker=_broker_url(), backend=_broker_url())

celery_app.conf.update(
    # --- Serialization -----------------------------------------------------
    # JSON only. Pickle can execute arbitrary code on deserialization, which turns anyone who
    # can write to the broker into someone who can run code on every worker.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # --- Reliability -------------------------------------------------------
    # Acknowledge AFTER the task finishes, not on receipt. If a worker is killed mid-task the
    # message is redelivered rather than lost. The cost is that a task can run twice, which is
    # why every job below must be idempotent — see the note in the schedule.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Fetch one message at a time. These jobs are slow and infrequent; prefetching would let one
    # worker sit on messages another could be running.
    worker_prefetch_multiplier=1,
    # A job that has not finished in 10 minutes is wedged. The soft limit raises inside the task
    # so it can clean up; the hard limit kills it.
    task_soft_time_limit=600,
    task_time_limit=660,
    # Results are only used for debugging here; expire them so Redis does not fill with them.
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)

# --- Beat schedule ----------------------------------------------------------
#
# Every task below is idempotent by construction: each re-reads current state and acts only on
# rows still needing action, so a redelivery after `task_acks_late` re-does nothing harmful.
# That property is load-bearing — do not add a task here without it.
celery_app.conf.beat_schedule = {
    "vendor-order-reminders": {
        "task": "app.tasks.vendor_order_reminders",
        "schedule": float(VENDOR_REMINDER_INTERVAL_SECONDS),
    },
    "delivery-sla-monitor": {
        "task": "app.tasks.delivery_sla_monitor",
        "schedule": float(DELIVERY_SLA_MONITOR_INTERVAL_SECONDS),
    },
    "promo-expiry-reminders": {
        "task": "app.tasks.promo_expiry_reminders",
        "schedule": float(PROMO_REMINDER_INTERVAL_SECONDS),
    },
    "payment-reconciliation": {
        "task": "app.tasks.payment_reconciliation",
        "schedule": float(PAYMENT_RECONCILIATION_INTERVAL_SECONDS),
    },
    "delivery-auto-release": {
        "task": "app.tasks.delivery_auto_release",
        "schedule": float(DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS),
    },
}

# Imported for the side effect of registering the tasks. Deferred to the bottom because
# `app.tasks` imports `celery_app` from here.
celery_app.autodiscover_tasks(["app"], related_name="tasks", force=True)
