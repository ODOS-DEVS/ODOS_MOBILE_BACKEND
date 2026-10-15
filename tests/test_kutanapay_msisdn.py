"""Phone numbers reach KutanaPay in the form their checkout page can parse.

The page renders a literal "+" and appends whatever it is given. Hand it the
local "0557337520" and the field reads "+0557337520", which is a number in no
country's plan -- so the network selector cannot identify MTN, silently falls
back to its first option (Airtel), and sends the approval prompt to a network
the number does not belong to. Nothing errors. The customer waits for a prompt
that is never coming and the checkout expires.

That cost a live test. These pin the conversion.
"""

from __future__ import annotations

import pytest

from app.services.kutanapay_service import normalize_msisdn


class TestGhanaianNumbers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0557337520", "233557337520"),   # MTN
            ("0241234567", "233241234567"),   # MTN
            ("0202972072", "233202972072"),   # Telecel
            ("0271234567", "233271234567"),   # AirtelTigo
        ],
    )
    def test_trunk_zero_becomes_the_country_code(self, raw, expected):
        assert normalize_msisdn(raw) == expected

    def test_the_plus_is_left_off_because_the_page_supplies_it(self):
        """Returning "+233..." would render as "++233..."."""
        assert not normalize_msisdn("0557337520").startswith("+")


class TestAlreadyInternational:
    @pytest.mark.parametrize(
        "raw",
        ["+233557337520", "233557337520", "00233557337520"],
    )
    def test_country_code_is_not_doubled(self, raw):
        assert normalize_msisdn(raw) == "233557337520"

    def test_a_bare_subscriber_number_gets_the_country_code(self):
        assert normalize_msisdn("557337520") == "233557337520"


class TestFormatting:
    @pytest.mark.parametrize(
        "raw",
        ["233 55 733 7520", "055-733-7520", "(055) 733 7520", " 0557337520 "],
    )
    def test_separators_are_stripped(self, raw):
        """The page concatenates, so a surviving space lands in the field."""
        assert normalize_msisdn(raw) == "233557337520"


class TestRefusesToGuess:
    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "+++"])
    def test_nothing_usable_is_none_not_a_broken_string(self, raw):
        assert normalize_msisdn(raw) is None

    def test_a_foreign_number_is_not_forced_to_ghana(self):
        """Guessing a country code is how you charge a stranger. A UK number
        is cleaned and passed through, never prefixed with 233."""
        result = normalize_msisdn("+44 7700 900123")
        assert result == "447700900123"
        assert not result.startswith("233")


class TestGatewayBoundary:
    def test_create_checkout_sends_the_normalized_number(self, monkeypatch):
        """Normalising at the boundary means every caller -- orders and wallet
        top-ups alike -- gets it right without repeating the conversion."""
        from app.core.config import settings
        from app.services import kutanapay_service as svc

        monkeypatch.setattr(settings, "kutanapay_api_key", "mk_test")
        captured = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": {"id": "chk_1", "checkout_url": "https://x/c"}}

        def fake_post(_url, json, headers, timeout):
            captured.update(json)
            return _Response()

        monkeypatch.setattr(svc.requests, "post", fake_post)

        svc.create_checkout(
            amount=1.0,
            reference="odos-x",
            customer_email="a@b.c",
            customer_phone="0557337520",
        )

        assert captured["customer_phone"] == "233557337520"

    def test_no_phone_sends_no_phone_field(self, monkeypatch):
        from app.core.config import settings
        from app.services import kutanapay_service as svc

        monkeypatch.setattr(settings, "kutanapay_api_key", "mk_test")
        captured = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": {"id": "chk_1", "checkout_url": "https://x/c"}}

        def fake_post(_url, json, headers, timeout):
            captured.update(json)
            return _Response()

        monkeypatch.setattr(svc.requests, "post", fake_post)

        svc.create_checkout(
            amount=1.0,
            reference="odos-x",
            customer_email="a@b.c",
            customer_phone=None,
        )

        assert "customer_phone" not in captured
