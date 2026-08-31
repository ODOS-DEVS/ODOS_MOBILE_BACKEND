"""The money guarantees that only real Postgres can prove.

Every safeguard exercised here lives in the database, not the application:
partial unique indexes, row locks, constraint scoping. A unit test cannot see
any of them, and SQLite would accept writes that Postgres rejects — which is
precisely the false confidence these exist to remove.

Each test states the real-world failure it prevents.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


# --- settlement: a vendor must never be paid twice for one order ---


def test_double_settlement_is_rejected_by_the_database(db, make_order, make_vendor_wallet):
    """The failure this prevents: an order settles, a retry or a second delivery
    confirmation fires, and the vendor is credited twice for one sale. The
    application checks for an existing row first, but that check is a
    read-then-write — under concurrency both callers can pass it. The partial
    unique index is what actually makes it impossible."""
    from app.models import VendorWalletTransaction

    wallet = make_vendor_wallet()
    order = make_order()

    def settlement_row():
        return VendorWalletTransaction(
            wallet_id=wallet.id,
            vendor_user_id=wallet.vendor_user_id,
            order_id=order.id,
            kind="sale_settlement",
            title="Settled",
            amount=90.0,
            balance_after=90.0,
        )

    db.add(settlement_row())
    db.flush()

    db.add(settlement_row())
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_refund_reversal_is_exempt_from_the_per_order_uniqueness(
    db, make_order, make_vendor_wallet
):
    """An order with several returned items produces one refund_reversal per
    return. Scoping uniqueness to (vendor, order, kind) without excluding that
    kind would reject every refund on an order after the first — a customer
    owed two refunds would silently receive one."""
    from app.models import VendorWalletTransaction

    wallet = make_vendor_wallet()
    order = make_order()

    for _ in range(2):
        db.add(
            VendorWalletTransaction(
                wallet_id=wallet.id,
                vendor_user_id=wallet.vendor_user_id,
                order_id=order.id,
                kind="refund_reversal",
                title="Refund reversed",
                amount=-10.0,
                balance_after=0.0,
            )
        )
    db.flush()  # must not raise


# --- ledger: one collection per payment ---


def test_ledger_rejects_a_second_collection_for_one_payment(db, make_order):
    """This is the constraint that was silently protecting the payment
    confirmation path before it took a row lock: a webhook and a client verify
    arriving together both tried to collect, and this stopped the second."""
    from app.models import PaymentTransaction, PlatformLedgerEntry, PlatformTreasuryAccount

    order = make_order()
    account = PlatformTreasuryAccount(currency="GHS")
    db.add(account)
    db.flush()

    payment = PaymentTransaction(
        order_id=order.id,
        user_id=order.user_id,
        provider="paystack",
        reference=f"odos-{uuid.uuid4().hex}",
        currency="GHS",
        amount_subunit=10000,
        status="paid",
    )
    db.add(payment)
    db.flush()

    def entry():
        return PlatformLedgerEntry(
            treasury_account_id=account.id,
            payment_transaction_id=payment.id,
            order_id=order.id,
            kind="payment_collected",
            direction="in",
            title="Payment collected",
            amount=100.0,
            current_balance_after=100.0,
            vendor_liability_balance_after=90.0,
            commission_balance_after=10.0,
        )

    db.add(entry())
    db.flush()

    db.add(entry())
    with pytest.raises(IntegrityError):
        db.flush()


def test_payment_reference_is_unique(db, make_order):
    """Two payment attempts sharing a reference would let one Paystack
    confirmation resolve the wrong order."""
    from app.models import PaymentTransaction

    order = make_order()
    reference = f"odos-{uuid.uuid4().hex}"

    for _ in range(2):
        db.add(
            PaymentTransaction(
                order_id=order.id,
                user_id=order.user_id,
                provider="paystack",
                reference=reference,
                currency="GHS",
                amount_subunit=10000,
                status="pending",
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()


def test_duplicate_webhook_digest_is_rejected(db):
    """Paystack retries aggressively. Without this, one delivered event could be
    processed as many times as it is delivered."""
    from app.models import PaymentWebhookEvent

    digest = uuid.uuid4().hex

    for _ in range(2):
        db.add(
            PaymentWebhookEvent(
                provider="paystack",
                event_type="charge.success",
                event_digest=digest,
                reference="odos-ref",
                payload={},
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()


# --- concurrency: the row lock, exercised with real parallel sessions ---


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs a real database")
def test_concurrent_withdrawals_cannot_overspend_a_balance(engine):
    """The classic marketplace theft: fire two withdrawals for the full balance
    at the same moment and, without a lock, both read the same balance, both
    pass the check, and the vendor withdraws twice what they have.

    Uses genuinely separate connections — a row lock is invisible within one
    session. That also means this test cannot use the rolled-back `db` fixture:
    its savepoint commits are not visible to other connections, so the data has
    to be committed for real and cleaned up at the end.
    """
    from app.models import User, VendorWallet

    eng = create_engine(TEST_DATABASE_URL, future=True)
    with Session(eng) as setup:
        vendor = User(
            full_name="Concurrency Vendor",
            email=f"vendor-{uuid.uuid4().hex[:12]}@example.com",
            role="vendor",
        )
        setup.add(vendor)
        setup.flush()
        setup.add(VendorWallet(vendor_user_id=vendor.id, available_balance=100.0))
        setup.commit()
        vendor_id = vendor.id

    results: list[str] = []
    barrier = threading.Barrier(2)

    def attempt_withdrawal(amount: float) -> None:
        worker_engine = create_engine(TEST_DATABASE_URL, future=True)
        with Session(worker_engine) as session:
            try:
                barrier.wait(timeout=10)
                locked = session.scalar(
                    select(VendorWallet)
                    .where(VendorWallet.vendor_user_id == vendor_id)
                    .with_for_update()
                )
                if locked is None:
                    results.append("missing")
                    return
                if locked.available_balance < amount:
                    results.append("rejected")
                    session.rollback()
                    return
                locked.available_balance = round(locked.available_balance - amount, 2)
                session.commit()
                results.append("approved")
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion
                results.append(f"error:{exc.__class__.__name__}")

    threads = [threading.Thread(target=attempt_withdrawal, args=(100.0,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Exactly one may succeed. Two approvals means the lock is not doing its job
    # and a vendor could withdraw twice their balance.
    assert results.count("approved") == 1, results
    assert results.count("rejected") == 1, results

    # And the balance must reflect exactly one withdrawal.
    with Session(eng) as check:
        final = check.scalar(
            select(VendorWallet.available_balance).where(
                VendorWallet.vendor_user_id == vendor_id
            )
        )
    assert final == 0.0, f"balance should be 0 after one withdrawal, got {final}"

    # Clean up: this test committed, so the fixture rollback cannot undo it.
    with Session(eng) as cleanup:
        cleanup.execute(
            text("DELETE FROM vendor_wallets WHERE vendor_user_id = :v"), {"v": vendor_id}
        )
        cleanup.execute(text("DELETE FROM users WHERE id = :v"), {"v": vendor_id})
        cleanup.commit()
