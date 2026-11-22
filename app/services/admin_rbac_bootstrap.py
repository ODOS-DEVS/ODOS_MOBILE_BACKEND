"""Bringing the RBAC tables up to date. Safe to run on every boot.

Three steps, in order, all idempotent:

1. Sync the permission catalogue from code into `admin_permissions`.
2. Create the default roles if they are absent.
3. Once, and only once, give every existing administrator the role that
   reproduces the permission band they already had.

Why step 3 only runs once
-------------------------
It is guarded on the whole `admin_user_roles` table being empty. The moment
anyone anywhere has been assigned a role, the platform has started managing
access deliberately and a backfill would be interfering — specifically, it would
re-grant a role a Super Admin had just taken away. Checking "this user has no
roles" instead of "nobody has any roles" would do exactly that, which is why the
guard is global rather than per-user.

Super admins are skipped. Their privilege is the `admin_permission` flag and
never a role, so giving them one would imply the flag was replaceable.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import User, UserRole
from app.models.admin_rbac import AdminRole, AdminUserRole
from app.services.admin_rbac import RBAC_MANAGED_FLAG, SUPER_ADMIN_FLAG
from app.services.admin_rbac_seed import seed_default_roles

logger = logging.getLogger(__name__)

#: Old band -> the role that reproduces it. Bands that no longer exist, or an
#: administrator with no band at all, fall back to `migrated_admin`, which is
#: what `resolve_admin_permission` already treated a blank band as.
BAND_TO_ROLE_SLUG: dict[str, str] = {
    "admin": "migrated_admin",
    "support": "migrated_support",
    "finance": "migrated_finance",
    "inventory": "migrated_inventory",
    "marketing": "migrated_marketing",
    "moderator": "migrated_moderator",
    "analyst": "migrated_analyst",
}


def backfill_existing_admins(db: Session) -> dict[str, object]:
    """Give current administrators the role matching their old band."""
    already_assigned = db.scalar(select(func.count()).select_from(AdminUserRole)) or 0
    if already_assigned:
        return {"skipped": True, "reason": "roles are already being managed", "assigned": 0}

    roles = {role.slug: role for role in db.scalars(select(AdminRole)).all()}
    admins = list(
        db.scalars(select(User).where(User.role == UserRole.ADMIN)).all()
    )

    assigned = 0
    per_role: dict[str, int] = {}
    skipped_super = 0

    for admin in admins:
        band = (admin.admin_permission or "").strip().lower()
        if band == SUPER_ADMIN_FLAG:
            skipped_super += 1
            continue
        slug = BAND_TO_ROLE_SLUG.get(band, "migrated_admin")
        role = roles.get(slug)
        if role is None:
            logger.warning("admin RBAC backfill: role %s missing, skipping %s", slug, admin.id)
            continue
        db.add(AdminUserRole(user_id=admin.id, role_id=role.id))
        # Mark the account as role-governed from here on. The role just
        # assigned reproduces the band exactly, so nothing about their access
        # changes today — but removing that role tomorrow now revokes access
        # instead of quietly reinstating the band.
        admin.admin_permission = RBAC_MANAGED_FLAG
        assigned += 1
        per_role[slug] = per_role.get(slug, 0) + 1

    db.commit()
    return {
        "skipped": False,
        "assigned": assigned,
        "per_role": per_role,
        "super_admins_left_alone": skipped_super,
    }


#: Arbitrary but fixed key for the Postgres advisory lock below. Any constant
#: works; it only has to be the same in every worker and not collide with
#: another advisory lock in the codebase.
_BOOTSTRAP_LOCK_KEY = 572301001


def bootstrap_admin_rbac(db: Session, *, wait: bool = False) -> dict[str, object]:
    """Seed and backfill under an advisory lock.

    The lock matters because this runs at startup and the API serves from
    several uvicorn workers: without it, two workers racing to insert the same
    role slug would have one of them die on the unique constraint. The loser
    simply skips — the winner's work is what everyone needed.

    `wait=False` is right for startup (a worker that loses the race has nothing
    to do). The bootstrap script passes `wait=True` so an operator running it by
    hand always gets a real result rather than a silent skip.
    """
    lock_fn = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
    acquired = db.execute(
        text(f"SELECT {lock_fn}(:key)"), {"key": _BOOTSTRAP_LOCK_KEY}
    ).scalar()
    if not wait and acquired is False:
        logger.info("admin RBAC bootstrap: another worker holds the lock, skipping")
        return {"skipped": True, "reason": "another process is bootstrapping"}

    try:
        seeded = seed_default_roles(db)
        backfilled = backfill_existing_admins(db)
    except Exception:
        db.rollback()
        raise

    result = {"seed": seeded, "backfill": backfilled}
    logger.info("admin RBAC bootstrap: %s", result)
    return result
