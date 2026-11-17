"""The booking lifecycle, and who is allowed to move it.

Two separate questions, deliberately kept apart:

  1. Is this transition legal at all?  -> TRANSITIONS
  2. Is *this actor* allowed to make it? -> ALLOWED_ACTORS

Both are answered here rather than in the controllers, so the mobile app, the
website and the admin dashboard cannot disagree about what a booking may do —
and so a customer cannot mark their own booking `completed` to force a payout,
or a provider quietly flip a dispute back to `completed`.

Modelled on `app/services/delivery_state_machine.py`, which does the same job
for deliveries.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Terminal states. Nothing leaves these except an admin opening a dispute.
TERMINAL_STATUSES = frozenset({"completed", "cancelled", "declined", "expired"})

#: status -> the statuses it may legally become.
TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_provider": frozenset(
        {"pending_payment", "confirmed", "declined", "cancelled", "expired"}
    ),
    # 'confirmed' direct from pending_provider covers services taking payment
    # after the work: acceptance is all that is needed to hold the slot.
    "pending_payment": frozenset({"confirmed", "cancelled", "expired"}),
    "confirmed": frozenset({"in_progress", "completed", "cancelled", "disputed"}),
    "in_progress": frozenset({"completed", "cancelled", "disputed"}),
    "completed": frozenset({"disputed"}),
    "cancelled": frozenset(),
    "declined": frozenset(),
    "expired": frozenset(),
    # A dispute is resolved by an admin either way.
    "disputed": frozenset({"completed", "cancelled"}),
}

#: (from, to) -> roles permitted to make that move.
#: Admin is granted everything legal at the end of this module rather than
#: being written into every row.
ALLOWED_ACTORS: dict[tuple[str, str], frozenset[str]] = {
    ("pending_provider", "pending_payment"): frozenset({"provider"}),
    ("pending_provider", "confirmed"): frozenset({"provider"}),
    ("pending_provider", "declined"): frozenset({"provider"}),
    ("pending_provider", "cancelled"): frozenset({"customer"}),
    ("pending_provider", "expired"): frozenset({"system"}),
    ("pending_payment", "confirmed"): frozenset({"system"}),
    ("pending_payment", "cancelled"): frozenset({"customer", "provider"}),
    ("pending_payment", "expired"): frozenset({"system"}),
    ("confirmed", "in_progress"): frozenset({"provider"}),
    ("confirmed", "completed"): frozenset({"provider"}),
    ("confirmed", "cancelled"): frozenset({"customer", "provider"}),
    ("confirmed", "disputed"): frozenset({"customer"}),
    ("in_progress", "completed"): frozenset({"provider"}),
    ("in_progress", "cancelled"): frozenset({"provider"}),
    ("in_progress", "disputed"): frozenset({"customer"}),
    ("completed", "disputed"): frozenset({"customer"}),
    ("disputed", "completed"): frozenset(),
    ("disputed", "cancelled"): frozenset(),
}

#: Only payment confirmation moves a booking out of `pending_payment`, and
#: only the backend's own verification may do it. A client claiming success
#: is not a payment; see the payment verification flow.
SYSTEM_ONLY_TRANSITIONS = frozenset(
    {
        ("pending_payment", "confirmed"),
        ("pending_provider", "expired"),
        ("pending_payment", "expired"),
    }
)


class BookingTransitionError(ValueError):
    """An illegal transition, or a legal one attempted by the wrong party."""


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    reason: str | None = None


def can_transition(
    current: str, target: str, *, actor_role: str
) -> TransitionCheck:
    """Whether `actor_role` may move a booking from `current` to `target`."""

    if current == target:
        return TransitionCheck(False, "The booking is already in that state.")

    legal = TRANSITIONS.get(current)
    if legal is None:
        return TransitionCheck(False, f"Unknown booking status “{current}”.")
    if target not in legal:
        return TransitionCheck(
            False, f"A {current.replace('_', ' ')} booking cannot become {target.replace('_', ' ')}."
        )

    # Admins may make any transition the machine itself permits. They cannot
    # invent one: the legality check above still applies.
    if actor_role == "admin":
        return TransitionCheck(True)

    if (current, target) in SYSTEM_ONLY_TRANSITIONS and actor_role != "system":
        return TransitionCheck(
            False, "Only Odos can make that change."
        )

    permitted = ALLOWED_ACTORS.get((current, target), frozenset())
    if actor_role not in permitted:
        return TransitionCheck(
            False, "You do not have permission to make that change."
        )

    return TransitionCheck(True)


def assert_transition(current: str, target: str, *, actor_role: str) -> None:
    """Raise unless the transition is legal for this actor."""

    check = can_transition(current, target, actor_role=actor_role)
    if not check.allowed:
        raise BookingTransitionError(check.reason or "That change is not allowed.")


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def releases_slot(status: str) -> bool:
    """Whether reaching `status` returns the time slot to the calendar.

    The database's exclusion constraint is written against the blocking
    statuses, so this is informational for callers that want to know whether
    a slot just freed up — the guarantee itself does not depend on it.
    """

    return status in {"cancelled", "declined", "expired"}
