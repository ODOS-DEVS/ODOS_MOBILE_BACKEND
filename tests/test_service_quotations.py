"""Quote-first flow: the price comes from the provider, once, and expires.

The quoted amount is the only figure in the services domain that originates
from a human rather than the catalogue, so these concentrate on the ways that
could be abused — a customer naming their own price, a stale quote being
honoured, a quote being accepted twice.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.service import Service, ServiceQuotation
from app.schemas.service import QuotationRequestWrite, QuotationSendWrite
from app.services.service_quotation_service import (
    DEFAULT_QUOTE_VALIDITY_DAYS,
    QuotationError,
    _assert_acceptable,
    decline_quotation,
    send_quotation,
)


def make_quotation(**overrides) -> ServiceQuotation:
    q = ServiceQuotation(
        reference="QTE-100001",
        service_id="svc_1",
        store_id="store_1",
        customer_user_id=None,
        status=overrides.pop("status", "requested"),
        customer_brief="Rewire a three-bedroom house",
        currency="GHS",
    )
    q.quoted_amount = overrides.pop("quoted_amount", None)
    q.expires_at = overrides.pop("expires_at", None)
    for k, v in overrides.items():
        setattr(q, k, v)
    return q


class TestCustomerCannotSetThePrice:
    def test_request_schema_has_no_amount(self):
        assert "amount" not in QuotationRequestWrite.model_fields
        assert "quoted_amount" not in QuotationRequestWrite.model_fields

    def test_injected_amount_is_ignored(self):
        payload = QuotationRequestWrite.model_validate(
            {"brief": "Rewire a three-bedroom house", "amount": 1}
        )
        assert not hasattr(payload, "amount")

    def test_brief_must_be_substantive(self):
        """A two-word brief cannot be priced, so it is refused up front."""
        with pytest.raises(ValueError):
            QuotationRequestWrite.model_validate({"brief": "fix it"})


class TestSendingAQuote:
    def test_sets_amount_and_an_expiry(self):
        q = make_quotation()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)

        send_quotation(None, q, amount=2500.0, now=now)

        assert q.status == "sent"
        assert q.quoted_amount == 2500.0
        assert q.expires_at == now + timedelta(days=DEFAULT_QUOTE_VALIDITY_DAYS)

    def test_provider_can_set_a_shorter_validity(self):
        q = make_quotation()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        send_quotation(None, q, amount=100.0, validity_days=2, now=now)
        assert q.expires_at == now + timedelta(days=2)

    def test_a_quote_can_be_revised_before_acceptance(self):
        q = make_quotation()
        send_quotation(None, q, amount=1000.0)
        send_quotation(None, q, amount=1200.0)
        assert q.quoted_amount == 1200.0

    def test_cannot_quote_an_accepted_request(self):
        """Revising after acceptance would change an agreed price."""
        q = make_quotation(status="accepted")
        with pytest.raises(QuotationError, match="no longer be quoted"):
            send_quotation(None, q, amount=1.0)

    def test_negative_amount_is_refused(self):
        q = make_quotation()
        with pytest.raises(QuotationError, match="amount"):
            send_quotation(None, q, amount=-5.0)

    def test_schema_refuses_a_negative_amount(self):
        with pytest.raises(ValueError):
            QuotationSendWrite.model_validate({"amount": -1})


class TestAcceptance:
    def test_a_sent_quote_is_acceptable(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        q = make_quotation(
            status="sent", quoted_amount=500.0, expires_at=now + timedelta(days=1)
        )
        _assert_acceptable(q, now)

    def test_an_expired_quote_is_refused(self):
        """The provider priced this weeks ago and may not still mean it."""
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        q = make_quotation(
            status="sent", quoted_amount=500.0, expires_at=now - timedelta(seconds=1)
        )
        with pytest.raises(QuotationError, match="expired"):
            _assert_acceptable(q, now)

    def test_cannot_accept_before_a_price_exists(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with pytest.raises(QuotationError, match="no quotation to accept"):
            _assert_acceptable(make_quotation(status="requested"), now)

    def test_cannot_accept_twice(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        q = make_quotation(status="accepted", quoted_amount=500.0)
        with pytest.raises(QuotationError, match="already accepted"):
            _assert_acceptable(q, now)

    def test_cannot_accept_a_declined_quote(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        q = make_quotation(status="declined", quoted_amount=500.0)
        with pytest.raises(QuotationError, match="no quotation to accept"):
            _assert_acceptable(q, now)


class TestClosing:
    def test_customer_declining_marks_declined(self):
        q = make_quotation(status="sent", quoted_amount=100.0)
        decline_quotation(q, by_provider=False)
        assert q.status == "declined"

    def test_provider_withdrawing_is_recorded_separately(self):
        """Who walked away matters when a provider's record is reviewed."""
        q = make_quotation(status="sent", quoted_amount=100.0)
        decline_quotation(q, by_provider=True)
        assert q.status == "withdrawn"

    def test_cannot_close_an_accepted_quote(self):
        q = make_quotation(status="accepted")
        with pytest.raises(QuotationError, match="already closed"):
            decline_quotation(q)
