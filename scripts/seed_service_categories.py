"""Seed the service-marketplace taxonomy.

`service_categories` is an admin-managed table by design — the model comment is explicit that
it is a table rather than a constant "so categories can be added from the admin dashboard
without shipping code to four applications". Nothing seeds it, so a fresh database has an empty
Services section and no provider can categorise a listing.

This is the starting set, chosen for the Ghanaian market ODOS serves. It is a starting point,
not a fixed list: the admin dashboard owns these afterwards, and anything added or renamed there
survives a re-run.

Safe to re-run. Matches on slug, inserts what is missing, and leaves existing rows alone — so
an admin's edits are never reverted by running this again.

Usage:
    .venv/bin/python scripts/seed_service_categories.py
"""

from __future__ import annotations

import os
import sys

# Matches scripts/create_admin.py: running `python scripts/x.py` puts scripts/ on sys.path, not
# the project root, so `import app` would fail without this.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.service import ServiceCategory

#: (slug, title, subtitle, icon, [subcategory titles])
CATEGORIES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "home-property",
        "Home & Property",
        "Plumbing, electrical, carpentry and general repairs",
        "home",
        ["Plumbing", "Electrical", "Carpentry", "Painting", "Masonry", "Air conditioning"],
    ),
    (
        "cleaning",
        "Cleaning",
        "Homes, offices and post-construction cleaning",
        "sparkles",
        ["Home cleaning", "Office cleaning", "Laundry & ironing", "Fumigation", "Post-construction"],
    ),
    (
        "beauty-personal-care",
        "Beauty & Personal Care",
        "Hair, nails, make-up and wellness",
        "scissors",
        ["Hairdressing", "Barbering", "Nails", "Make-up", "Massage & spa"],
    ),
    (
        "events",
        "Events & Entertainment",
        "Planning, decor, catering and performers",
        "party-popper",
        ["Event planning", "Decor & rentals", "Catering", "DJs & live music", "MC & hosting"],
    ),
    (
        "photography",
        "Photography & Video",
        "Shoots, coverage and editing",
        "camera",
        ["Portrait photography", "Event coverage", "Product photography", "Videography", "Editing"],
    ),
    (
        "repairs",
        "Repairs & Maintenance",
        "Phones, appliances, electronics and vehicles",
        "wrench",
        ["Phone repair", "Appliance repair", "Computer repair", "Generator servicing", "Auto mechanic"],
    ),
    (
        "education",
        "Education & Tutoring",
        "Private tuition, exam prep and skills training",
        "graduation-cap",
        ["Private tutoring", "Exam preparation", "Music lessons", "Language lessons", "Skills training"],
    ),
    (
        "professional",
        "Professional Services",
        "Accounting, legal, consulting and admin",
        "briefcase",
        ["Accounting & tax", "Legal services", "Business consulting", "HR & recruitment", "Virtual assistance"],
    ),
    (
        "digital",
        "Technology & Digital",
        "Web, design, marketing and IT support",
        "laptop",
        ["Web development", "Graphic design", "Social media marketing", "IT support", "Data & analytics"],
    ),
    (
        "logistics",
        "Transport & Logistics",
        "Moving, courier work and vehicle hire",
        "truck",
        ["Moving & relocation", "Courier & errands", "Car hire with driver", "Haulage"],
    ),
    (
        "construction",
        "Construction & Trades",
        "Building, welding, tiling and roofing",
        "hard-hat",
        ["Building contractors", "Welding & fabrication", "Tiling", "Roofing", "Surveying"],
    ),
    (
        "food",
        "Catering & Food",
        "Private chefs, meal prep and bulk cooking",
        "chef-hat",
        ["Private chef", "Meal prep", "Bulk cooking", "Baking & pastries", "Bartending"],
    ),
]


def slugify(parent_slug: str, title: str) -> str:
    base = title.lower().replace("&", "and")
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in base)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return f"{parent_slug}-{cleaned.strip('-')}"[:80]


def main() -> None:
    db = SessionLocal()
    created = skipped = 0
    try:
        for order, (slug, title, subtitle, icon, children) in enumerate(CATEGORIES):
            parent = db.scalar(select(ServiceCategory).where(ServiceCategory.slug == slug))
            if parent is None:
                parent = ServiceCategory(
                    id=slug,
                    slug=slug,
                    title=title,
                    subtitle=subtitle,
                    icon=icon,
                    sort_order=order * 10,
                    is_active=True,
                )
                db.add(parent)
                db.flush()
                created += 1
            else:
                skipped += 1

            for child_order, child_title in enumerate(children):
                child_slug = slugify(slug, child_title)
                if db.scalar(select(ServiceCategory.id).where(ServiceCategory.slug == child_slug)):
                    skipped += 1
                    continue
                db.add(
                    ServiceCategory(
                        id=child_slug,
                        slug=child_slug,
                        title=child_title,
                        parent_id=parent.id,
                        sort_order=child_order * 10,
                        is_active=True,
                    )
                )
                created += 1

        db.commit()
        print(f"service categories: {created} created, {skipped} already present")
    finally:
        db.close()


if __name__ == "__main__":
    main()
