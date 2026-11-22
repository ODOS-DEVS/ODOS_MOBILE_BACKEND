"""Syncing the permission catalogue and seeding default roles. Idempotent.

Two jobs, both safe to run on every deploy:

1. `sync_permission_catalogue` mirrors the code catalogue into
   `admin_permissions`, so role rows can carry a foreign key. Codes that
   disappear from code are marked retired rather than deleted, because a role
   row or audit entry may still point at one.

2. `seed_default_roles` creates the roles below if they are absent, and tops up
   a system role's permissions if the catalogue has grown. It never removes a
   permission a Super Admin added by hand, and never touches a role's
   assignments — re-seeding must not quietly re-open access someone closed.

The migration problem, and what this does about it
--------------------------------------------------
The old system was one `users.admin_permission` string naming a band, and a band
granted whole modules: holding "finance" meant every finance route, including
recording a refund as paid. The new catalogue splits that into
`finance.view_refunds`, `finance.approve_refunds` and
`finance.process_refunds`.

Mapping a band onto the new clean roles would therefore either expand access
(if sensitive permissions came along) or silently shrink it (if they did not),
and a working administrator discovering in production that they can no longer do
their job is the worse outcome. So bands are mapped to their own `migrated_*`
roles that reproduce the old reach exactly, and the recommended least-privilege
roles are seeded alongside for everyone hired next. The Super Admin can move
people across deliberately and deactivate the migrated roles afterwards.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_permission_catalogue import (
    ALL_CODES,
    PERMISSIONS,
    PERMISSIONS_BY_MODULE,
    SENSITIVE_CODES,
)
from app.models.admin_rbac import AdminPermission, AdminRole, AdminRolePermission

logger = logging.getLogger(__name__)


def _codes_for_modules(modules: tuple[str, ...], *, include_sensitive: bool) -> set[str]:
    codes: set[str] = set()
    for module in modules:
        for permission in PERMISSIONS_BY_MODULE.get(module, ()):
            if permission.sensitive and not include_sensitive:
                continue
            codes.add(permission.code)
    return codes


def _view_only(modules: tuple[str, ...]) -> set[str]:
    """Read access across modules: anything whose action begins with `view`."""
    codes: set[str] = set()
    for module in modules:
        for permission in PERMISSIONS_BY_MODULE.get(module, ()):
            if permission.sensitive:
                continue
            if permission.action == "view" or permission.action.startswith("view_"):
                codes.add(permission.code)
    return codes


# ---------------------------------------------------------------------------
# The old bands, reproduced exactly.
# ---------------------------------------------------------------------------
# Module lists copied from PERMISSION_FEATURES in app/core/admin_permissions.py
# as it stood before this change. `super_admin` is absent on purpose: it stays a
# flag on the user, never a role.
LEGACY_BAND_MODULES: dict[str, tuple[str, ...]] = {
    "admin": (
        "dashboard", "analytics", "audit_log", "users", "vendors", "orders",
        "returns", "products", "stores", "markets", "categories", "guides",
        "promotions", "reviews", "notifications", "support", "delivery",
        "finance", "payouts",
        # Not a reproduction of the old band but a correction of it: the
        # services desk was guarded by a feature no band held, making it
        # super-admin-only by accident. Platform Administrator gets it.
        "services",
    ),
    "support": (
        "dashboard", "audit_log", "guides", "users", "orders", "returns",
        "notifications", "support",
    ),
    "finance": ("dashboard", "analytics", "audit_log", "finance", "payouts", "orders"),
    "inventory": (
        "dashboard", "audit_log", "products", "stores", "markets", "categories",
        "promotions",
    ),
    "marketing": (
        "dashboard", "analytics", "audit_log", "guides", "promotions",
        "notifications", "products",
    ),
    "moderator": (
        "dashboard", "audit_log", "products", "reviews", "vendors",
        "notifications", "support",
    ),
    "analyst": ("dashboard", "analytics", "audit_log"),
}

LEGACY_BAND_LABELS: dict[str, str] = {
    "admin": "Platform Administrator",
    "support": "Support",
    "finance": "Finance",
    "inventory": "Inventory & Catalogue",
    "marketing": "Marketing",
    "moderator": "Moderation",
    "analyst": "Analyst",
}


def _legacy_role_specs() -> list[dict]:
    specs = []
    for band, modules in LEGACY_BAND_MODULES.items():
        specs.append(
            {
                "slug": f"migrated_{band}",
                "name": f"{LEGACY_BAND_LABELS[band]} (migrated)",
                "description": (
                    f"Reproduces exactly what the old \"{band}\" permission band could reach, "
                    "so administrators carried over from it kept working. Includes the "
                    "sensitive actions that band allowed. Move people onto a least-privilege "
                    "role and deactivate this one when you are ready."
                ),
                # include_sensitive: the old band genuinely permitted these routes.
                "codes": _codes_for_modules(modules, include_sensitive=True),
            }
        )
    return specs


# ---------------------------------------------------------------------------
# Recommended roles. Least privilege: no sensitive permission is seeded.
# ---------------------------------------------------------------------------
RECOMMENDED_ROLES: tuple[dict, ...] = (
    {
        "slug": "vendor_manager",
        "name": "Vendor Manager",
        "description": "Reviews vendor applications and manages vendor accounts and stores. "
        "Verification documents are a separate permission and are not included.",
        "codes": lambda: {
            "dashboard.view", "vendors.view", "vendors.update", "vendors.approve",
            "vendors.reject", "vendors.suspend", "vendors.activate",
            "stores.view", "stores.update", "reviews.view",
        },
    },
    {
        "slug": "product_manager",
        "name": "Product & Catalogue Manager",
        "description": "Manages the catalogue: products, categories and product approvals. "
        "No access to finance or vendor identity documents.",
        "codes": lambda: {
            "dashboard.view", "products.view", "products.create", "products.update",
            "products.approve", "products.reject", "products.publish", "products.unpublish",
            "categories.view", "categories.create", "categories.update",
            "stores.view", "reviews.view",
        },
    },
    {
        "slug": "order_manager",
        "name": "Order Manager",
        "description": "Monitors and progresses orders. Cancelling and refunding are "
        "separate permissions and are not included.",
        "codes": lambda: {
            "dashboard.view", "orders.view", "orders.update", "orders.view_customer_details",
            "returns.view", "delivery.view",
        },
    },
    {
        "slug": "delivery_manager",
        "name": "Delivery & Logistics Manager",
        "description": "Runs dispatch and delivery. Cannot cancel orders or issue refunds.",
        "codes": lambda: {
            "dashboard.view", "delivery.view", "delivery.assign", "delivery.update_status",
            "delivery.manage_personnel", "delivery.view_proof", "delivery.export",
            "orders.view",
        },
    },
    {
        "slug": "support_manager",
        "name": "Customer Support Manager",
        "description": "Handles customer enquiries and complaints. Follows data minimisation: "
        "customer contact details and payment records are separate permissions.",
        "codes": lambda: {
            "dashboard.view", "support.view", "support.respond", "support.manage",
            "users.view", "orders.view", "returns.view", "returns.decide",
        },
    },
    {
        "slug": "finance_manager",
        "name": "Finance Manager",
        "description": "Reviews financial records and the refund ledger. Approving and "
        "recording refunds, and payouts, are separate permissions you grant deliberately.",
        "codes": lambda: {
            "dashboard.view", "analytics.view",
            "finance.view_dashboard", "finance.view_refunds", "payouts.view",
            "orders.view",
        },
    },
    {
        "slug": "marketing_manager",
        "name": "Marketing & Campaign Manager",
        "description": "Builds campaigns, banners and vouchers. Publishing is a separate "
        "permission so a campaign can be prepared without going live.",
        "codes": lambda: {
            "dashboard.view", "analytics.view",
            "promotions.view", "promotions.create", "promotions.update",
            "promotions.manage_nominations", "promotions.approve_submissions",
            "products.view", "notifications.view",
        },
    },
    {
        "slug": "content_manager",
        "name": "Content Manager",
        "description": "Manages homepage content, banners and guide videos. Publishing is "
        "a separate permission.",
        "codes": lambda: {
            "dashboard.view", "promotions.view", "promotions.create", "promotions.update",
            "guides.view", "guides.manage", "categories.view",
        },
    },
    {
        "slug": "services_manager",
        "name": "Services Manager",
        "description": "Runs the services marketplace: categories, provider applications "
        "and listing moderation.",
        "codes": lambda: {
            "dashboard.view", "services.view", "services.manage_categories",
            "services.approve_providers", "services.moderate", "services.resolve_disputes",
            "reviews.view",
        },
    },
    {
        "slug": "moderation_manager",
        "name": "Review & Moderation Manager",
        "description": "Moderates reviews, reported products, stores and services. "
        "No access to finance or account management.",
        "codes": lambda: {
            "dashboard.view", "reviews.view", "reviews.moderate",
            "products.view", "stores.view", "services.view", "support.view",
        },
    },
    {
        "slug": "analyst",
        "name": "Analyst (read only)",
        "description": "Read access to dashboards and reports. Holds no permission that "
        "changes a business record.",
        "codes": lambda: {"dashboard.view", "analytics.view"}
        | _view_only(("orders", "products", "vendors", "delivery")),
    },
)


def sync_permission_catalogue(db: Session) -> dict[str, int]:
    """Mirror the code catalogue into `admin_permissions`. Idempotent."""
    existing = {row.code: row for row in db.scalars(select(AdminPermission)).all()}
    created = updated = retired = 0

    for permission in PERMISSIONS:
        row = existing.get(permission.code)
        if row is None:
            db.add(
                AdminPermission(
                    code=permission.code,
                    module=permission.module,
                    action=permission.action,
                    label=permission.label,
                    description=permission.description,
                    is_sensitive=permission.sensitive,
                    is_retired=False,
                )
            )
            created += 1
            continue
        # Labels and descriptions are editorial and may be reworded in code;
        # let them flow through so the permission editor never shows stale copy.
        changed = (
            row.module != permission.module
            or row.action != permission.action
            or row.label != permission.label
            or row.description != permission.description
            or row.is_sensitive != permission.sensitive
            or row.is_retired
        )
        if changed:
            row.module = permission.module
            row.action = permission.action
            row.label = permission.label
            row.description = permission.description
            row.is_sensitive = permission.sensitive
            row.is_retired = False
            updated += 1

    for code, row in existing.items():
        if code not in ALL_CODES and not row.is_retired:
            # Kept, not deleted: a role row or audit entry may still reference
            # it, and resolution already ignores anything outside the catalogue.
            row.is_retired = True
            retired += 1

    db.flush()
    return {"created": created, "updated": updated, "retired": retired}


def _ensure_role(db: Session, spec: dict, codes: set[str]) -> tuple[AdminRole, bool, int]:
    role = db.scalar(select(AdminRole).where(AdminRole.slug == spec["slug"]))
    fresh = role is None
    if role is None:
        role = AdminRole(
            slug=spec["slug"],
            name=spec["name"],
            description=spec["description"],
            is_system=True,
            is_active=True,
        )
        db.add(role)
        db.flush()

    held = {row.permission_code for row in role.permissions}
    # Top up only. A permission a Super Admin removed by hand stays removed:
    # re-seeding on deploy must never silently re-open access someone closed.
    added = 0
    missing = (codes - held) if fresh else set()
    for code in sorted(missing):
        if code not in ALL_CODES:
            continue
        db.add(AdminRolePermission(role_id=role.id, permission_code=code))
        added += 1

    return role, fresh, added


def seed_default_roles(db: Session) -> dict[str, object]:
    """Create the migrated and recommended roles if absent. Idempotent."""
    catalogue = sync_permission_catalogue(db)

    created: list[str] = []
    topped_up: dict[str, int] = {}

    for spec in _legacy_role_specs():
        _, fresh, added = _ensure_role(db, spec, spec["codes"])
        if fresh:
            created.append(spec["slug"])
        if added and not fresh:
            topped_up[spec["slug"]] = added

    for spec in RECOMMENDED_ROLES:
        codes = spec["codes"]() if callable(spec["codes"]) else spec["codes"]
        unknown = codes - ALL_CODES
        if unknown:
            raise RuntimeError(
                f"Role {spec['slug']!r} seeds permissions that are not in the catalogue: "
                f"{sorted(unknown)}"
            )
        leaked = codes & SENSITIVE_CODES
        if leaked:
            raise RuntimeError(
                f"Recommended role {spec['slug']!r} must not seed sensitive permissions: "
                f"{sorted(leaked)}"
            )
        _, fresh, added = _ensure_role(db, spec, codes)
        if fresh:
            created.append(spec["slug"])
        if added and not fresh:
            topped_up[spec["slug"]] = added

    db.commit()
    result = {"catalogue": catalogue, "roles_created": created, "roles_topped_up": topped_up}
    logger.info("admin RBAC seed: %s", result)
    return result
