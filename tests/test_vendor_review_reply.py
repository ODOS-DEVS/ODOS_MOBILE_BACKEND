"""Unit tests for vendor review reply ownership checks."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
from fastapi import HTTPException

from app.controllers.vendor_controller import reply_to_vendor_review
from app.models import UserRole, VendorStatus
from app.schemas.vendor import VendorReviewReplyUpdate


def _vendor_user(**overrides):
    base = {
        "id": uuid.uuid4(),
        "role": UserRole.VENDOR,
        "vendor_status": VendorStatus.APPROVED,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _review_row(vendor_user_id: uuid.UUID):
    review = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_reply=None,
        vendor_replied_at=None,
        rating=4.5,
        comment="Great product!",
        is_hidden=False,
        created_at=datetime.now(timezone.utc),
    )
    product = SimpleNamespace(
        id="prod-1",
        title="Test Product",
        image_url=None,
        image_urls=None,
        vendor_user_id=vendor_user_id,
    )
    customer = SimpleNamespace(full_name="Jane Doe")
    return review, product, customer


def test_reply_to_review_rejects_when_product_not_owned_by_vendor():
    """A vendor cannot reply to a review on another vendor's product."""
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    user = _vendor_user()
    payload = VendorReviewReplyUpdate(reply="Thanks for the feedback!")

    with pytest.raises(HTTPException) as exc_info:
        reply_to_vendor_review(db, user, str(uuid.uuid4()), payload)

    assert exc_info.value.status_code == 404
    db.commit.assert_not_called()


def test_reply_to_review_succeeds_for_owning_vendor():
    """A vendor can reply to a review left on their own product."""
    db = MagicMock()
    vendor_id = uuid.uuid4()
    review, product, customer = _review_row(vendor_id)
    db.execute.return_value.first.return_value = (review, product, customer)

    user = _vendor_user(id=vendor_id)
    payload = VendorReviewReplyUpdate(reply="  Thanks for shopping with us!  ")

    result = reply_to_vendor_review(db, user, str(review.id), payload)

    assert result.vendor_reply == "Thanks for shopping with us!"
    assert result.vendor_replied_at is not None
    assert review.vendor_reply == "Thanks for shopping with us!"
    db.commit.assert_called_once()


def test_reply_to_review_rejects_invalid_review_id():
    db = MagicMock()
    user = _vendor_user()
    payload = VendorReviewReplyUpdate(reply="Thanks!")

    with pytest.raises(HTTPException) as exc_info:
        reply_to_vendor_review(db, user, "not-a-uuid", payload)

    assert exc_info.value.status_code == 404
    db.execute.assert_not_called()


def test_reply_to_review_requires_vendor_access():
    db = MagicMock()
    user = _vendor_user(vendor_status=VendorStatus.PENDING)
    payload = VendorReviewReplyUpdate(reply="Thanks!")

    with pytest.raises(HTTPException) as exc_info:
        reply_to_vendor_review(db, user, str(uuid.uuid4()), payload)

    assert exc_info.value.status_code == 403
    db.execute.assert_not_called()
