from __future__ import annotations

SUPPORTED_PROMO_PLACEMENTS = frozenset({"home", "deals"})
DEFAULT_PROMO_PLACEMENT = "home"

SUPPORTED_PROMO_LINK_TYPES = frozenset(
    {
        "deals",
        "flash_sales",
        "popular",
        "search",
        "category",
        "product",
        "store",
        "vouchers",
        "campaign",
        "external",
        "screen",
    }
)
DEFAULT_PROMO_LINK_TYPE = "deals"

PROMO_CAMPAIGN_TAGS: list[tuple[str, str]] = [
    ("christmas", "Christmas Deals"),
    ("easter", "Easter Offers"),
    ("eid", "Eid Specials"),
    ("valentine", "Valentine Offers"),
    ("black-friday", "Black Friday Ghana"),
    ("independence", "Independence Day Deals"),
    ("republic-day", "Republic Day Deals"),
    ("back-to-school", "Back to School"),
    ("payday", "Salary Week Deals"),
    ("student", "Student Deals"),
    ("free-delivery", "Free Delivery"),
    ("weekend-market", "ODOS Weekend Market"),
    ("made-in-ghana", "Made in Ghana Deals"),
    ("campus", "Campus Deals"),
    ("hot-deals", "Hot Deals Today"),
]

PROMO_DESTINATION_LABELS: dict[str, str] = {
    "deals": "Deals & offers hub",
    "flash_sales": "Flash sales",
    "popular": "Popular products",
    "search": "Search results",
    "category": "Category browse",
    "product": "Product page",
    "store": "Store page",
    "vouchers": "Voucher wallet",
    "campaign": "Seasonal campaign",
    "external": "Website link",
    "screen": "Custom app screen",
}

PROMO_PLACEMENT_LABELS: dict[str, str] = {
    "home": "Home screen carousel",
    "deals": "Deals & promos screen",
}


def normalize_promo_placement(value: str | None) -> str:
    cleaned = (value or DEFAULT_PROMO_PLACEMENT).strip().lower()
    return cleaned if cleaned in SUPPORTED_PROMO_PLACEMENTS else DEFAULT_PROMO_PLACEMENT


def normalize_promo_link_type(value: str | None) -> str:
    cleaned = (value or DEFAULT_PROMO_LINK_TYPE).strip().lower()
    return cleaned if cleaned in SUPPORTED_PROMO_LINK_TYPES else DEFAULT_PROMO_LINK_TYPE


def describe_promo_destination(
    *,
    link_type: str | None,
    cta_link: str | None,
    campaign_tag: str | None,
) -> str:
    resolved_type = normalize_promo_link_type(link_type)
    base = PROMO_DESTINATION_LABELS.get(resolved_type, "App destination")
    target = (cta_link or "").strip()
    tag = (campaign_tag or "").strip()

    if resolved_type == "campaign" and tag:
        tag_label = dict(PROMO_CAMPAIGN_TAGS).get(tag, tag.replace("-", " ").title())
        return f"{base} · {tag_label}"
    if resolved_type in {"category", "product", "store", "search", "external", "screen"} and target:
        return f"{base} · {target}"
    return base
