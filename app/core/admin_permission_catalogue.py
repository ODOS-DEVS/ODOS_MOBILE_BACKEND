"""Every administrative action ODOS can authorise, as a flat catalogue.

Why this lives in code
----------------------
What operations exist is a property of the application, not of the data: a new
permission appears because someone wrote a route, never because someone typed a
row. So this module is the source of truth, and `admin_permissions` in the
database is a mirror of it, synced idempotently (see
`app.services.admin_rbac_seed`). Roles then reference permissions by code
through a real foreign key, which is what keeps role rows honest.

The alternative — permissions as user-managed rows — means every deploy has to
reconcile the table with the code that reads it, and a typo'd code silently
grants nothing. This way the codes are checked at import time.

Naming
------
`module.action`, lower snake case. The module half matches the feature names the
existing `require_admin_feature` guards already use, so the coarse band system
and this fine-grained one describe the same territory and
`admin_has_feature` can keep answering from it unchanged.

Sensitivity
-----------
`sensitive=True` marks a permission that exposes identity documents, financial
operations, customer contact details or administrative access itself. These are
never included when a role is seeded from a template — they have to be granted
deliberately, which is the whole point of separating
`vendors.view` from `vendors.view_sensitive_documents`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    module: str
    action: str
    label: str
    description: str
    sensitive: bool = False


def _p(
    module: str,
    action: str,
    label: str,
    description: str,
    *,
    sensitive: bool = False,
) -> Permission:
    return Permission(
        code=f"{module}.{action}",
        module=module,
        action=action,
        label=label,
        description=description,
        sensitive=sensitive,
    )


#: Human-readable module names, in the order the permission editor shows them.
MODULE_LABELS: dict[str, str] = {
    "dashboard": "Dashboard",
    "analytics": "Analytics & reports",
    "vendors": "Vendors",
    "stores": "Stores",
    "products": "Products",
    "categories": "Categories",
    "markets": "Markets",
    "orders": "Orders",
    "returns": "Returns",
    "delivery": "Delivery & logistics",
    "finance": "Finance",
    "payouts": "Payouts",
    "users": "Customers",
    "support": "Customer support",
    "reviews": "Reviews & moderation",
    "promotions": "Campaigns & promotions",
    "services": "Services marketplace",
    "notifications": "Notifications",
    "guides": "Guide videos",
    "settings": "Platform settings",
    "admins": "Administration",
    "audit_log": "Audit logs",
}

PERMISSIONS: tuple[Permission, ...] = (
    # --- Dashboard -------------------------------------------------------
    _p("dashboard", "view", "View dashboard", "See the admin home dashboard and its summary figures."),

    # --- Analytics -------------------------------------------------------
    _p("analytics", "view", "View analytics", "Open analytics dashboards and platform performance reports."),
    _p("analytics", "export", "Export analytics", "Download analytics and report data as files."),

    # --- Vendors ---------------------------------------------------------
    _p("vendors", "view", "View vendors", "Browse vendor accounts, applications and store profiles."),
    _p("vendors", "update", "Edit vendors", "Change vendor account and store details."),
    _p("vendors", "approve", "Approve applications", "Accept a vendor application and let them start selling."),
    _p("vendors", "reject", "Reject applications", "Decline a vendor application."),
    _p("vendors", "suspend", "Suspend vendors", "Take a vendor offline and hide their listings."),
    _p("vendors", "activate", "Reactivate vendors", "Return a suspended vendor to active selling."),
    _p(
        "vendors", "view_sensitive_documents", "View verification documents",
        "See Ghana Card numbers and business registration documents submitted for verification.",
        sensitive=True,
    ),
    _p("vendors", "export", "Export vendor records", "Download vendor lists and application data.", sensitive=True),

    # --- Stores ----------------------------------------------------------
    _p("stores", "view", "View stores", "Browse storefronts and their settings."),
    _p("stores", "update", "Edit stores", "Change storefront details, sections and visibility."),
    _p("stores", "suspend", "Suspend stores", "Hide a storefront from shoppers."),

    # --- Products --------------------------------------------------------
    _p("products", "view", "View products", "Browse the product catalogue and submissions."),
    _p("products", "create", "Create products", "Add products to the catalogue."),
    _p("products", "update", "Edit products", "Change product details, pricing and stock."),
    _p("products", "delete", "Delete products", "Remove products from the catalogue."),
    _p("products", "approve", "Approve products", "Pass a submitted product for sale."),
    _p("products", "reject", "Reject products", "Decline a submitted product."),
    _p("products", "publish", "Publish products", "Make an approved product visible to shoppers."),
    _p("products", "unpublish", "Unpublish products", "Hide a live product from shoppers."),
    _p("products", "export", "Export products", "Download catalogue data."),

    # --- Categories ------------------------------------------------------
    _p("categories", "view", "View categories", "Browse categories and subcategories."),
    _p("categories", "create", "Create categories", "Add categories and subcategories."),
    _p("categories", "update", "Edit categories", "Rename and reorganise categories."),
    _p("categories", "delete", "Delete categories", "Remove categories and subcategories."),

    # --- Markets ---------------------------------------------------------
    _p("markets", "view", "View markets", "Browse markets and their locations."),
    _p("markets", "create", "Create markets", "Add a market."),
    _p("markets", "update", "Edit markets", "Change market details."),
    _p("markets", "delete", "Delete markets", "Remove a market."),

    # --- Orders ----------------------------------------------------------
    _p("orders", "view", "View orders", "Browse and search orders, and open order details."),
    _p("orders", "update", "Update orders", "Change order fulfilment details where permitted."),
    _p("orders", "cancel", "Cancel orders", "Cancel an order outright."),
    _p(
        "orders", "decide_cancellation", "Decide cancellation requests",
        "Approve or decline a cancellation request a vendor has left unanswered.",
    ),
    # Not flagged sensitive: the delivery address *is* the order. Nobody can
    # coordinate a dispatch without it, and lumping it in with identity
    # documents and money movements only dilutes what the flag means. Bulk
    # extraction is the sensitive act, and that is `orders.export`.
    _p(
        "orders", "view_customer_details", "View order customer details",
        "See the shopper's name, phone and delivery address on an order.",
    ),
    _p("orders", "export", "Export orders", "Download order data.", sensitive=True),

    # --- Returns ---------------------------------------------------------
    _p("returns", "view", "View returns", "Browse return and exchange requests."),
    _p("returns", "decide", "Decide returns", "Approve or decline a return request."),

    # --- Delivery --------------------------------------------------------
    _p("delivery", "view", "View deliveries", "Monitor shipments and delivery progress."),
    _p("delivery", "assign", "Assign deliveries", "Hand a delivery to a rider or courier."),
    _p("delivery", "update_status", "Update delivery status", "Move a delivery through its stages manually."),
    _p("delivery", "manage_personnel", "Manage delivery personnel", "Add, edit and deactivate riders."),
    _p("delivery", "view_proof", "View proof of delivery", "See dispatch and hand-over photographs."),
    _p("delivery", "manage_settings", "Manage delivery settings", "Change delivery fees, zones and rules."),
    _p("delivery", "export", "Export delivery data", "Download delivery and rider reports."),

    # --- Finance ---------------------------------------------------------
    _p("finance", "view_dashboard", "View finance dashboard", "See the finance overview and its headline figures."),
    _p("finance", "view_transactions", "View transactions", "Browse individual payment transactions.", sensitive=True),
    _p("finance", "view_vendor_earnings", "View vendor earnings", "See what each vendor has earned.", sensitive=True),
    _p("finance", "view_commissions", "View commissions", "See platform commission figures.", sensitive=True),
    _p("finance", "view_refunds", "View refunds", "Browse the refund ledger."),
    _p(
        "finance", "approve_refunds", "Approve refunds",
        "Authorise a refund for payout, without sending the money.",
        sensitive=True,
    ),
    _p(
        "finance", "process_refunds", "Record refunds as paid",
        "Confirm money was actually transferred back, with the provider reference.",
        sensitive=True,
    ),
    _p("finance", "export", "Export financial data", "Download transaction and reconciliation files.", sensitive=True),

    # --- Payouts ---------------------------------------------------------
    _p("payouts", "view", "View payouts", "Browse vendor withdrawal requests and payout history."),
    _p("payouts", "approve", "Approve payouts", "Authorise a vendor withdrawal.", sensitive=True),
    _p("payouts", "process", "Process payouts", "Record a withdrawal as paid out.", sensitive=True),

    # --- Customers -------------------------------------------------------
    _p("users", "view", "View customers", "Browse customer accounts."),
    _p("users", "update", "Edit customers", "Change customer account details."),
    _p("users", "suspend", "Suspend customers", "Disable a customer account."),
    _p(
        "users", "view_sensitive_information", "View customer contact details",
        "See full email addresses, phone numbers and saved addresses.",
        sensitive=True,
    ),
    _p("users", "export", "Export customer records", "Download customer data.", sensitive=True),

    # --- Support ---------------------------------------------------------
    _p("support", "view", "View support conversations", "Read customer support threads."),
    _p("support", "respond", "Respond to support", "Reply to customers in support threads."),
    _p("support", "manage", "Manage support queue", "Assign, close and escalate support threads."),

    # --- Reviews & moderation --------------------------------------------
    _p("reviews", "view", "View reviews & reports", "Browse reviews and reported content."),
    _p("reviews", "moderate", "Moderate content", "Hide, restore or action reviews and reported content."),
    _p("reviews", "delete", "Delete reviews", "Permanently remove a review."),

    # --- Campaigns & promotions ------------------------------------------
    _p("promotions", "view", "View campaigns & promotions", "Browse campaigns, banners, vouchers and flash sales."),
    _p("promotions", "create", "Create campaigns & promotions", "Add campaigns, banners, vouchers and flash sales."),
    _p("promotions", "update", "Edit campaigns & promotions", "Change campaign and promotion details."),
    _p("promotions", "delete", "Delete campaigns & promotions", "Remove a campaign, banner or voucher."),
    _p("promotions", "publish", "Publish promotions", "Make a campaign or banner live to shoppers."),
    _p("promotions", "manage_nominations", "Manage vendor nominations", "Open and close vendor submission windows."),
    _p("promotions", "approve_submissions", "Approve vendor submissions", "Accept or decline products vendors nominated."),
    _p("promotions", "manage_negotiations", "Moderate price negotiations", "Review negotiations and switch haggling off."),

    # --- Services marketplace --------------------------------------------
    # Previously guarded by a "catalog" feature that belonged to no permission
    # band, which made the entire services desk super-admin-only by accident.
    _p("services", "view", "View services", "Browse service listings, providers and bookings."),
    _p("services", "manage_categories", "Manage service categories", "Add and edit service categories."),
    _p("services", "approve_providers", "Approve service providers", "Accept or decline provider applications."),
    _p("services", "moderate", "Moderate services", "Hide or action service listings and content."),
    _p("services", "resolve_disputes", "Resolve service disputes", "Settle a dispute between a customer and provider."),

    # --- Notifications ---------------------------------------------------
    _p("notifications", "view", "View notifications", "See notification history."),
    _p("notifications", "send", "Send notifications", "Broadcast notifications to users."),

    # --- Guides ----------------------------------------------------------
    _p("guides", "view", "View guide videos", "Browse guide and onboarding videos."),
    _p("guides", "manage", "Manage guide videos", "Add, edit and remove guide videos."),

    # --- Platform settings -----------------------------------------------
    _p("settings", "view", "View settings", "See platform configuration."),
    _p("settings", "update", "Change settings", "Modify platform configuration.", sensitive=True),

    # --- Administration --------------------------------------------------
    _p("admins", "view", "View administrators", "See the administrator list and their roles."),
    _p("admins", "invite", "Invite administrators", "Send an administrator invitation.", sensitive=True),
    _p("admins", "update", "Edit administrators", "Change an administrator's name and details.", sensitive=True),
    _p("admins", "deactivate", "Deactivate administrators", "Disable or suspend an administrator account.", sensitive=True),
    _p("admins", "assign_roles", "Assign roles", "Change which roles an administrator holds.", sensitive=True),
    _p("admins", "manage_permissions", "Manage permissions", "Grant or revoke individual permission overrides.", sensitive=True),
    _p("admins", "manage_roles", "Manage role definitions", "Create, edit and retire roles.", sensitive=True),

    # --- Audit -----------------------------------------------------------
    _p("audit_log", "view", "View audit logs", "Read the record of administrative actions."),
    _p("audit_log", "export", "Export audit logs", "Download audit records.", sensitive=True),
)

#: code -> Permission, for O(1) validation.
PERMISSIONS_BY_CODE: dict[str, Permission] = {p.code: p for p in PERMISSIONS}

#: Every module that has at least one permission, in display order.
MODULES: tuple[str, ...] = tuple(MODULE_LABELS.keys())

#: Codes grouped by module, in catalogue order.
PERMISSIONS_BY_MODULE: dict[str, tuple[Permission, ...]] = {
    module: tuple(p for p in PERMISSIONS if p.module == module) for module in MODULES
}

ALL_CODES: frozenset[str] = frozenset(PERMISSIONS_BY_CODE)
SENSITIVE_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS if p.sensitive)


def _validate() -> None:
    """Catch catalogue mistakes at import, not at a 403 in production."""
    if len(PERMISSIONS_BY_CODE) != len(PERMISSIONS):
        seen: set[str] = set()
        dupes = {p.code for p in PERMISSIONS if p.code in seen or seen.add(p.code)}  # type: ignore[func-returns-value]
        raise RuntimeError(f"Duplicate permission codes in catalogue: {sorted(dupes)}")
    unknown = {p.module for p in PERMISSIONS} - set(MODULE_LABELS)
    if unknown:
        raise RuntimeError(f"Permissions reference modules with no label: {sorted(unknown)}")
    empty = [m for m, items in PERMISSIONS_BY_MODULE.items() if not items]
    if empty:
        raise RuntimeError(f"Modules declared with no permissions: {sorted(empty)}")


_validate()
