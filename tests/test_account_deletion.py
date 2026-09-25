"""Account deletion: what blocks it, and what survives it.

The flow is destructive and irreversible, so the behaviour worth pinning down
is not that it works but that it refuses when it should, and that it leaves the
commerce records intact when it proceeds.
"""



from app.models import Order, SavedAddress, User
from app.services.account_deletion_service import delete_account, deletion_blockers


def test_clean_account_has_no_blockers(db, make_user):
    user = make_user()
    assert deletion_blockers(db, user) == []


def test_order_in_flight_blocks_deletion(db, make_user, make_order):
    user = make_user()
    order = make_order(user=user)
    order.status = "processing"
    db.flush()

    blockers = deletion_blockers(db, user)
    assert len(blockers) == 1
    assert "in progress" in blockers[0]


def test_delivered_order_does_not_block(db, make_user, make_order):
    """A finished order is a record, not an obligation."""
    user = make_user()
    order = make_order(user=user)
    order.status = "delivered"
    db.flush()

    assert deletion_blockers(db, user) == []


def test_customer_wallet_balance_blocks_deletion(db, make_user):
    from app.models import CustomerWallet

    user = make_user()
    db.add(CustomerWallet(user_id=user.id, available_balance=25.0))
    db.flush()

    blockers = deletion_blockers(db, user)
    assert any("wallet" in b for b in blockers)


def test_vendor_owed_money_blocks_deletion(db, make_vendor_wallet):
    wallet = make_vendor_wallet(balance=400.0)
    vendor = db.get(User, wallet.vendor_user_id)

    blockers = deletion_blockers(db, vendor)
    assert any("vendor wallet" in b for b in blockers)


def test_unfulfilled_package_blocks_vendor_deletion(db, make_user, make_order):
    from app.models import OrderPackage

    vendor = make_user(role="vendor")
    order = make_order()
    db.add(
        OrderPackage(
            order_id=order.id,
            vendor_user_id=vendor.id,
            vendor_status="confirmed",
            items_subtotal=50.0,
        )
    )
    db.flush()

    blockers = deletion_blockers(db, vendor)
    assert any("fulfil" in b for b in blockers)


def test_all_blockers_are_reported_together(db, make_user, make_order):
    """The app shows every reason at once rather than one refusal at a time."""
    from app.models import CustomerWallet

    user = make_user()
    order = make_order(user=user)
    order.status = "pending"
    db.add(CustomerWallet(user_id=user.id, available_balance=10.0))
    db.flush()

    assert len(deletion_blockers(db, user)) == 2


def test_deletion_anonymises_the_person(db, make_user):
    user = make_user()
    user.phone_number = "0244000000"
    user.avatar_url = "https://example.com/me.jpg"
    user.city = "Accra"
    original_id = user.id
    db.flush()

    delete_account(db, user)

    refreshed = db.get(User, original_id)
    assert refreshed is not None, "the row must survive; orders hang off it"
    assert refreshed.full_name == "Deleted user"
    assert refreshed.email.endswith("@odos.invalid")
    assert refreshed.phone_number is None
    assert refreshed.avatar_url is None
    assert refreshed.city is None
    assert refreshed.deleted_at is not None


def test_deletion_locks_the_account_out(db, make_user):
    user = make_user()
    user.hashed_password = "not-a-real-hash"
    before = user.token_version or 0
    db.flush()

    delete_account(db, user)

    assert user.is_active is False, "is_active gates login and token validation"
    assert user.hashed_password is None
    assert user.token_version == before + 1, "existing tokens must stop working"


def test_deletion_keeps_orders(db, make_user, make_order):
    """The vendor's sales record must not develop holes when a customer leaves."""
    user = make_user()
    order = make_order(user=user)
    order.status = "delivered"
    order_id = order.id
    db.flush()

    delete_account(db, user)

    assert db.get(Order, order_id) is not None


def test_deletion_removes_personal_rows(db, make_user):
    user = make_user()
    db.add(SavedAddress(
        user_id=user.id, full_name="Test Person", phone="0200000000",
        street="1 Test Street", city="Accra", region="Greater Accra",
    ))
    db.flush()

    delete_account(db, user)

    remaining = db.query(SavedAddress).filter(SavedAddress.user_id == user.id).count()
    assert remaining == 0


def test_two_deleted_accounts_do_not_collide(db, make_user):
    """email is unique, so the placeholder has to differ per account."""
    first, second = make_user(), make_user()
    db.flush()

    delete_account(db, first)
    delete_account(db, second)

    assert first.email != second.email
