"""The resource-center guide catalog the mobile app currently ships.

This file is *data about another repo*. The guides themselves live in
`constants/resourceGuides.ts` in odos-mobile-expo and are bundled into the
app; `app/data/guide_catalog.json` is a generated snapshot of their slugs,
titles and step names, refreshed by running

    npm run export:guide-catalog

in the mobile repo and committing the result here.

It exists purely so the admin panel can offer a real list to attach a video
to instead of a free-text slug box. Nothing depends on it being current: an
admin can always type a slug by hand, and a slug that only one side knows
about just means no video renders.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "guide_catalog.json"


@lru_cache(maxsize=1)
def load_guide_catalog() -> list[dict]:
    """Guides the shipped app knows about, or an empty list if the snapshot is
    missing or malformed. Never raises — a broken snapshot must not take the
    admin API down, it should only cost the convenience of a dropdown."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(
            "Guide catalog snapshot missing at %s — the admin guide picker will "
            "be empty until `npm run export:guide-catalog` is run in the mobile repo.",
            CATALOG_PATH,
        )
        return []
    except (OSError, json.JSONDecodeError):
        logger.exception("Guide catalog snapshot at %s could not be read", CATALOG_PATH)
        return []

    guides = raw.get("guides") if isinstance(raw, dict) else None
    if not isinstance(guides, list):
        logger.warning("Guide catalog snapshot at %s has no 'guides' list", CATALOG_PATH)
        return []

    return [guide for guide in guides if isinstance(guide, dict) and guide.get("slug")]


def known_guide_slugs() -> set[str]:
    return {str(guide["slug"]) for guide in load_guide_catalog()}
