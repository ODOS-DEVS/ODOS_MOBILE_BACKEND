"""Unit tests for vendor wallet access control (dual-role sellers)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.controllers.wallet_controller import _require_approved_vendor
from app.models import UserRole, VendorStatus


def _user(**overrides):
    base = {
        "role": UserRole.CUSTOMER,
        "vendor_status": VendorStatus.NONE,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_approved_customer_role_seller_can_access_wallet():
    """Dual-role sellers (role=customer, vendor_status=approved) must access the wallet."""
    user = _user(role=UserRole.CUSTOMER, vendor_status=VendorStatus.APPROVED)

    # Should not raise.
    _require_approved_vendor(user)


def test_approved_vendor_role_seller_can_access_wallet():
    user = _user(role=UserRole.VENDOR, vendor_status=VendorStatus.APPROVED)

    _require_approved_vendor(user)


def test_admin_can_access_wallet_regardless_of_vendor_status():
    user = _user(role=UserRole.ADMIN, vendor_status=VendorStatus.NONE)

    _require_approved_vendor(user)


def test_unapproved_customer_role_seller_is_rejected():
    user = _user(role=UserRole.CUSTOMER, vendor_status=VendorStatus.PENDING)

    with pytest.raises(HTTPException) as exc_info:
        _require_approved_vendor(user)

    assert exc_info.value.status_code == 403


def test_suspended_seller_is_rejected():
    user = _user(role=UserRole.VENDOR, vendor_status=VendorStatus.SUSPENDED)

    with pytest.raises(HTTPException) as exc_info:
        _require_approved_vendor(user)

    assert exc_info.value.status_code == 403
