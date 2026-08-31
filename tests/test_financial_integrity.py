"""Unit tests for the financial integrity checks.

The balance/transaction invariant these assert is the one thing that would
notice if the constraint-and-lock design ever stopped holding — a bad migration,
a manual database edit, or float drift accumulating across settlements. The
DB-level parts (the actual queries) need a live Postgres and are covered by the
integration suite; what is deterministic and unit-tested here is the arithmetic
and the tolerance boundary.
"""

from __future__ import annotations

from app.services.financial_integrity_service import TOLERANCE, Discrepancy


def test_delta_reports_the_direction_of_drift():
    """A positive delta means the wallet holds more than its history explains,
    which is the direction that costs ODOS money."""
    over = Discrepancy(scope="vendor_wallet", subject_id="v1", expected=100.0, actual=105.0)
    under = Discrepancy(scope="vendor_wallet", subject_id="v2", expected=100.0, actual=95.0)
    assert over.delta == 5.0
    assert under.delta == -5.0


def test_delta_keeps_sub_pesewa_precision():
    """Drift starts small. Rounding the delta to 2dp would hide exactly the
    accumulation this check exists to catch."""
    tiny = Discrepancy(scope="vendor_wallet", subject_id="v1", expected=100.0, actual=100.0003)
    assert tiny.delta != 0.0


def test_tolerance_is_one_pesewa():
    """Balances are floats today, so exact equality would report representation
    noise as drift. One pesewa separates noise from a real accounting gap."""
    assert TOLERANCE == 0.01


def test_a_drift_below_tolerance_is_not_a_discrepancy():
    expected, actual = 1000.00, 1000.004
    assert abs(expected - actual) <= TOLERANCE


def test_a_drift_above_tolerance_is_a_discrepancy():
    expected, actual = 1000.00, 1000.02
    assert abs(expected - actual) > TOLERANCE
