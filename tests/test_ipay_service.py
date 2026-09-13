"""iPay collection helpers.

The gateway offers no signed webhook and no server-to-server initiate, so the
safety of this integration rests entirely on three small pure functions: the
reference must fit iPay's field, the amount must survive the GHS<->pesewa round
trip exactly, and an unrecognised status must never read as success.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import ipay_service
from app.services.ipay_service import (
    INVOICE_ID_MAX_LENGTH,
    build_checkout_fields,
    generate_invoice_id,
    normalize_status,
    parse_amount_to_subunit,
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ipay_merchant_key", "test-merchant-key", raising=False)
    monkeypatch.setattr(settings, "ipay_merchant_code", "TESTCODE", raising=False)
    yield


# --- reference ------------------------------------------------------------

def test_invoice_id_fits_the_gateway_field():
    # iPay rejects anything over 25 chars, and the Paystack reference
    # (odos-{uuid4().hex}) is 37 -- reusing it would fail every checkout.
    for _ in range(200):
        assert len(generate_invoice_id()) <= INVOICE_ID_MAX_LENGTH


def test_invoice_ids_do_not_repeat():
    assert len({generate_invoice_id() for _ in range(2000)}) == 2000


# --- amounts --------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12.34", 1234),
        ("1.00", 100),
        ("0.01", 1),
        ("100", 10000),
        (12.34, 1234),
        ("0.00", 0),
        ("1999.99", 199999),
    ],
)
def test_amounts_convert_exactly(raw, expected):
    # int(12.34 * 100) is 1233 in binary floating point. A pesewa lost here
    # fails the equality check in the IPN handler and rejects a real payment.
    assert parse_amount_to_subunit(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "abc", "12.3.4", {}])
def test_unparseable_amounts_return_none_rather_than_zero(raw):
    # None is refused by the caller; a silent 0 would compare equal to a
    # zero-total order and could release goods for free.
    assert parse_amount_to_subunit(raw) is None


# --- status ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("paid", "paid"),
        ("PAID", "paid"),
        ("  paid  ", "paid"),
        ("cancelled", "cancelled"),
        ("new", "pending"),
        ("awaiting_payment", "pending"),
        ("failed", "failed"),
    ],
)
def test_known_statuses_map_across(raw, expected):
    assert normalize_status(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "something_new", "success", "ok", 0, True])
def test_unknown_statuses_are_never_treated_as_paid(raw):
    # A gateway that adds a status later must not accidentally confirm orders.
    assert normalize_status(raw) != "paid"


# --- checkout fields ------------------------------------------------------

def test_checkout_fields_are_refused_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "ipay_merchant_key", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        build_checkout_fields(
            invoice_id="odos123",
            total="1.00",
            success_url="https://x/s",
            cancelled_url="https://x/c",
            ipn_url="https://x/ipn",
        )
    assert exc.value.status_code == 503


def test_checkout_fields_carry_what_the_gateway_requires(configured):
    fields = build_checkout_fields(
        invoice_id="odos123",
        total="12.50",
        success_url="https://x/s",
        cancelled_url="https://x/c",
        ipn_url="https://x/ipn",
        customer_name="Jobby",
        customer_email="j@example.com",
        customer_mobile="0240000000",
        description="ODOS order 1001",
    )
    for required in ("merchant_key", "invoice_id", "total", "success_url", "cancelled_url", "ipn_url"):
        assert required in fields, required
    assert fields["merchant_key"] == "test-merchant-key"
    # GHS decimal, not pesewas -- sending 1250 here would charge 100x.
    assert fields["total"] == "12.50"


def test_optional_customer_fields_are_omitted_when_absent(configured):
    fields = build_checkout_fields(
        invoice_id="odos123",
        total="1.00",
        success_url="https://x/s",
        cancelled_url="https://x/c",
        ipn_url="https://x/ipn",
    )
    assert "extra_name" not in fields
    assert "extra_email" not in fields


def test_an_oversized_invoice_id_is_rejected_before_it_reaches_the_gateway(configured):
    with pytest.raises(HTTPException) as exc:
        build_checkout_fields(
            invoice_id="x" * 40,
            total="1.00",
            success_url="https://x/s",
            cancelled_url="https://x/c",
            ipn_url="https://x/ipn",
        )
    assert exc.value.status_code == 500


# --- status check transport ----------------------------------------------

def test_unreachable_gateway_raises_rather_than_reporting_unpaid(configured, monkeypatch):
    # A timeout must not be indistinguishable from "customer did not pay",
    # or a network blip would cancel paid orders.
    import requests

    def boom(*args, **kwargs):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(ipay_service.requests, "get", boom)
    with pytest.raises(HTTPException) as exc:
        ipay_service.check_status("odos123")
    assert exc.value.status_code == 502


def test_unreadable_response_raises(configured, monkeypatch):
    class FakeResponse:
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(ipay_service.requests, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(HTTPException) as exc:
        ipay_service.check_status("odos123")
    assert exc.value.status_code == 502


def test_non_dict_response_raises(configured, monkeypatch):
    class FakeResponse:
        def json(self):
            return ["unexpected"]

    monkeypatch.setattr(ipay_service.requests, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(HTTPException) as exc:
        ipay_service.check_status("odos123")
    assert exc.value.status_code == 502
