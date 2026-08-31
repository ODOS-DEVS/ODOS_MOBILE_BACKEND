"""Database-level guarantees for store sections.

The two that matter: a shop cannot end up with duplicate shelves, and one shop's
sections are not reachable from another shop. Both are enforced by constraints
and by scoping in the controller, and neither is visible without a real
database.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def make_store(db, make_user):
    from app.models import Store

    def _make(category: str = "Fashion"):
        vendor = make_user(role="vendor")
        store = Store(
            id=f"store-{uuid.uuid4().hex[:12]}",
            slug=f"store-{uuid.uuid4().hex[:12]}",
            title="Test Store",
            category=category,
            image_key="stores/test.png",
            vendor_user_id=vendor.id,
        )
        db.add(store)
        db.flush()
        return store

    return _make


def test_one_shop_cannot_have_two_shelves_with_the_same_name(db, make_store):
    """Without this a vendor's stock splits silently across two shelves that
    look identical on their own screen."""
    from app.models import StoreSection

    store = make_store()
    for _ in range(2):
        db.add(
            StoreSection(store_id=store.id, title="Shirts", slug="shirts", sort_order=1)
        )
    with pytest.raises(IntegrityError):
        db.flush()


def test_two_different_shops_may_both_have_a_shirts_shelf(db, make_store):
    """Uniqueness is per store. Scoping it globally would mean the first shop to
    create "Shirts" owned that name platform-wide."""
    from app.models import StoreSection

    first, second = make_store(), make_store()
    db.add(StoreSection(store_id=first.id, title="Shirts", slug="shirts"))
    db.add(StoreSection(store_id=second.id, title="Shirts", slug="shirts"))
    db.flush()  # must not raise


def test_a_product_cannot_be_placed_on_the_same_shelf_twice(db, make_store):
    """A duplicate join row would show the same item twice on the store page."""
    from app.models import Product, StoreSection, StoreSectionProduct

    store = make_store()
    section = StoreSection(store_id=store.id, title="Shirts", slug="shirts")
    db.add(section)
    product = Product(
        id=f"prod-{uuid.uuid4().hex[:12]}",
        title="A shirt",
        price=1000,
        image_key="products/shirt.png",
        store_id=store.id,
    )
    db.add(product)
    db.flush()

    for _ in range(2):
        db.add(StoreSectionProduct(section_id=section.id, product_id=product.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_deleting_a_shelf_does_not_delete_its_products(db, make_store):
    """Removing a shelf unshelves items; it must never destroy stock. The
    cascade is on the join rows only."""
    from app.models import Product, StoreSection, StoreSectionProduct

    store = make_store()
    section = StoreSection(store_id=store.id, title="Shirts", slug="shirts")
    db.add(section)
    product = Product(
        id=f"prod-{uuid.uuid4().hex[:12]}",
        title="A shirt",
        price=1000,
        image_key="products/shirt.png",
        store_id=store.id,
    )
    db.add(product)
    db.flush()
    db.add(StoreSectionProduct(section_id=section.id, product_id=product.id))
    db.flush()

    db.delete(section)
    db.flush()

    assert db.get(Product, product.id) is not None
    remaining = db.query(StoreSectionProduct).filter_by(section_id=section.id).count()
    assert remaining == 0
