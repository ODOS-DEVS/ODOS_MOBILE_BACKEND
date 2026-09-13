"""Approving a vendor application must not overflow stores.description.

A VendorApplication holds up to 1000 characters in both store_description and
business_description. Store.description is VARCHAR(255). The approval path
copied store_description across untruncated, so any applicant who wrote more
than 255 characters made Postgres raise StringDataRightTruncation.

That surfaced as a bare 500 with no CORS headers, which the admin SPA reported
as "We couldn't reach the ODOS backend" -- so the failure looked like an outage
rather than a validation bug, and the application simply could not be approved.

255 is also what the vendor-facing PATCH /store enforces, so truncating on the
way in keeps a single limit for the column.
"""

from __future__ import annotations

import types

from app.controllers.vendor_controller import (
    STORE_DESCRIPTION_MAX_LENGTH,
    _store_description_from,
)


def application(store_description, business_description="fallback description"):
    return types.SimpleNamespace(
        store_description=store_description,
        business_description=business_description,
    )


def test_long_store_description_is_truncated_to_the_column_width():
    result = _store_description_from(application("x" * 380))
    assert len(result) == STORE_DESCRIPTION_MAX_LENGTH
    assert result == "x" * STORE_DESCRIPTION_MAX_LENGTH


def test_long_business_description_is_truncated_when_store_description_is_absent():
    result = _store_description_from(application(None, "y" * 1000))
    assert len(result) == STORE_DESCRIPTION_MAX_LENGTH


def test_short_description_is_left_alone():
    assert _store_description_from(application("We sell wardrobes.")) == "We sell wardrobes."


def test_store_description_wins_over_business_description():
    assert _store_description_from(application("store", "business")) == "store"


def test_blank_store_description_falls_back_to_business_description():
    assert _store_description_from(application("", "business")) == "business"


def test_both_missing_yields_empty_string_rather_than_raising():
    # business_description is non-null in the model, but the old code did
    # business_description[:255] unguarded -- a None there was a TypeError.
    assert _store_description_from(application(None, None)) == ""


def test_surrounding_whitespace_is_stripped():
    assert _store_description_from(application("  padded  ")) == "padded"


def test_truncation_counts_characters_not_bytes():
    # The application that exposed this bug contained emoji.
    result = _store_description_from(application("✅" * 300))
    assert len(result) == STORE_DESCRIPTION_MAX_LENGTH
