"""Unit tests for store section naming and starter suggestions.

Ownership scoping and uniqueness are enforced by the database and the vendor
controller, and are covered in the integration suite; what is deterministic and
testable without a database is how titles become slugs, and which shelves a shop
gets offered.
"""

from __future__ import annotations

import pytest

from app.services.store_section_service import (
    FALLBACK_SECTIONS,
    STARTER_SECTIONS,
    slugify_section,
    starter_sections_for_category,
)


# --- slugs decide what counts as the same shelf ---


@pytest.mark.parametrize(
    "first,second",
    [
        ("T-Shirts", "t shirts"),
        ("T-Shirts", "T-SHIRTS"),
        ("Personal Development", "personal-development"),
        ("Shoes ", " Shoes"),
    ],
)
def test_titles_that_mean_the_same_shelf_share_a_slug(first, second):
    """Uniqueness is (store_id, slug), so this is what stops a vendor ending up
    with "T-Shirts" and "t shirts" as two separate shelves holding half their
    stock each."""
    assert slugify_section(first) == slugify_section(second)


def test_distinct_shelves_keep_distinct_slugs():
    assert slugify_section("Shirts") != slugify_section("T-Shirts")


def test_punctuation_and_ampersands_survive_as_readable_slugs():
    assert slugify_section("Business & Forex") == "business-forex"


def test_a_title_of_only_punctuation_still_yields_a_usable_slug():
    """An empty slug would violate the unique constraint on the second such
    section and surface as a 500 rather than a validation message."""
    assert slugify_section("!!!") == "section"


def test_slug_is_bounded_to_the_column_width():
    assert len(slugify_section("x" * 200)) <= 80


# --- starter suggestions ---


def test_a_bookshop_is_offered_book_shelves():
    titles = starter_sections_for_category("SALJAYS BOOKSHOP")
    assert "Personal Development" in titles
    assert "Comics" in titles


def test_a_fashion_store_is_offered_clothing_shelves():
    titles = starter_sections_for_category("Fashion & Style")
    assert "Shirts" in titles
    assert "Trousers" in titles


def test_matching_is_loose_enough_for_free_text_categories():
    """Vendors type their own category, so matching cannot require an exact
    key — "Ladies Clothing" and "clothing" should land in the same place."""
    assert starter_sections_for_category("Ladies Clothing") == STARTER_SECTIONS["fashion"]


def test_an_unrecognised_category_still_gets_something_useful():
    assert starter_sections_for_category("Hardware") == FALLBACK_SECTIONS


def test_a_store_with_no_category_gets_the_fallback():
    assert starter_sections_for_category(None) == FALLBACK_SECTIONS


def test_suggestions_are_copies_so_a_caller_cannot_mutate_the_defaults():
    """These lists are module-level constants. Handing out the original would
    let one request's edits change what every later shop is offered."""
    first = starter_sections_for_category("books")
    first.append("Tampered")
    assert "Tampered" not in starter_sections_for_category("books")
