"""Database fixtures for the integration suite.

The guarantees that actually keep ODOS's money correct — partial unique
indexes, SELECT ... FOR UPDATE, deferred constraint checks — are invisible to
any test that does not run against real Postgres. SQLite silently accepts
several of them and enforces none.

So these fixtures require a real database and skip rather than pretend when one
is not configured. Point TEST_DATABASE_URL at a throwaway instance:

    docker run -d --name odos-test-pg \\
      -e POSTGRES_USER=odos_user -e POSTGRES_PASSWORD=odos_local_password \\
      -e POSTGRES_DB=odos_mobile -p 55432:5432 postgres:18-alpine

    TEST_DATABASE_URL=postgresql+psycopg://odos_user:odos_local_password@localhost:55432/odos_mobile \\
      .venv/bin/python -m pytest tests/integration -q

Each test runs inside a transaction that is rolled back afterwards, so the
suite is order-independent and leaves nothing behind.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; see tests/conftest.py for how to start one",
)


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.connect() as conn:
        # Fail loudly if the schema was never migrated, rather than producing a
        # confusing cascade of "relation does not exist" further down.
        exists = conn.execute(
            text("SELECT to_regclass('public.vendor_wallet_transactions')")
        ).scalar()
        if not exists:
            pytest.skip(
                "Test database has no schema. Run: "
                "DATABASE_URL=$TEST_DATABASE_URL .venv/bin/python -m alembic upgrade head"
            )
    return eng


@pytest.fixture
def db(engine):
    """A session wrapped in a transaction that is always rolled back.

    Nested inside an outer transaction so that code under test may call
    commit() — which the financial services do — without persisting anything.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def make_user(db):
    from app.models import User

    def _make(role: str = "customer") -> User:
        user = User(
            full_name="Test Person",
            email=f"test-{uuid.uuid4().hex[:12]}@example.com",
            role=role,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def make_order(db, make_user):
    from app.models import Order

    def _make(user=None, total: float = 100.0) -> Order:
        owner = user or make_user()
        order = Order(
            order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
            user_id=owner.id,
            subtotal_amount=total,
            total_amount=total,
            address_full_name="Test Person",
            address_phone="0200000000",
            address_street="1 Test Street",
            address_city="Accra",
            address_region="Greater Accra",
            payment_type="card",
            payment_label="Card",
        )
        db.add(order)
        db.flush()
        return order

    return _make


@pytest.fixture
def make_vendor_wallet(db, make_user):
    from app.models import VendorWallet

    def _make(balance: float = 0.0):
        vendor = make_user(role="vendor")
        wallet = VendorWallet(vendor_user_id=vendor.id, available_balance=balance)
        db.add(wallet)
        db.flush()
        return wallet

    return _make
