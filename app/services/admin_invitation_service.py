"""Inviting an administrator.

Why invitations rather than created accounts
-------------------------------------------
The existing `POST /admin/staff` had the Super Admin type a password on the new
administrator's behalf, which means the account's credentials were known to
someone else from the moment it existed and there was no way for the holder to
know that had stopped being true. An invitation inverts it: the account has no
password until the person named in the email sets one.

Token handling
--------------
The raw token is generated once, returned once to the caller that emails it, and
never stored — only its SHA-256. So a database leak cannot be used to accept
invitations, and the token cannot turn up in a log, a backup or an audit row.
Acceptance looks the account up by hashing what it was given.

`secrets.token_urlsafe(32)` is 256 bits of entropy, and comparison is done by
hash equality on an indexed column rather than by scanning candidates.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.admin_rbac import (
    INVITE_EXPIRED,
    INVITE_PENDING,
    AdminInvitation,
)

logger = logging.getLogger(__name__)

#: How long an invitation stays usable. Long enough to survive a weekend,
#: short enough that a forgotten invitation in a mailbox stops being a way in.
INVITATION_TTL_HOURS = 72

#: A resend is allowed, but not as a way to send mail on demand.
MAX_SENDS_PER_INVITATION = 5
RESEND_COOLDOWN_SECONDS = 120


def generate_token() -> tuple[str, str]:
    """Returns `(raw_token, token_hash)`. The raw half is never persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def invitation_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=INVITATION_TTL_HOURS)


def acceptance_url(raw_token: str) -> str:
    """Where the invitee goes to set a password.

    Built from `settings.admin_panel_url`, so a deployment that has not set
    that correctly produces a link to the wrong host — worth checking before
    relying on invitations in production.
    """
    base = settings.admin_panel_url.rstrip("/")
    return f"{base}/accept-invitation?token={raw_token}"


def find_pending_by_token(db: Session, raw_token: str) -> AdminInvitation | None:
    """The live invitation this token belongs to, or None.

    Returns None for a token that is unknown, already used, revoked or past its
    expiry — the caller cannot tell which, and deliberately so.
    """
    if not (raw_token or "").strip():
        return None
    invitation = db.scalar(
        select(AdminInvitation).where(AdminInvitation.token_hash == hash_token(raw_token))
    )
    if invitation is None or invitation.status != INVITE_PENDING:
        return None
    if is_expired(invitation):
        return None
    return invitation


def is_expired(invitation: AdminInvitation) -> bool:
    expires = invitation.expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


def expire_stale_invitations(db: Session) -> int:
    """Mark passed-expiry invitations expired so the list reads honestly.

    Acceptance already refuses them on the date, so this is bookkeeping rather
    than a security control — but an invitation shown as "pending" three weeks
    after it lapsed is a lie the administrators list should not tell.
    """
    rows = db.scalars(
        select(AdminInvitation).where(AdminInvitation.status == INVITE_PENDING)
    ).all()
    changed = 0
    for invitation in rows:
        if is_expired(invitation):
            invitation.status = INVITE_EXPIRED
            changed += 1
    if changed:
        db.commit()
    return changed


def can_resend(invitation: AdminInvitation) -> tuple[bool, str | None]:
    """Whether another invitation email may go out right now."""
    if invitation.status != INVITE_PENDING:
        return False, "Only a pending invitation can be resent."
    if is_expired(invitation):
        return False, "This invitation has expired. Revoke it and invite again."
    if (invitation.send_count or 0) >= MAX_SENDS_PER_INVITATION:
        return False, (
            "This invitation has been sent the maximum number of times. "
            "Revoke it and invite again."
        )
    last = invitation.last_sent_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        waited = (datetime.now(UTC) - last).total_seconds()
        if waited < RESEND_COOLDOWN_SECONDS:
            remaining = int(RESEND_COOLDOWN_SECONDS - waited)
            return False, f"Please wait {remaining} more seconds before resending."
    return True, None


def send_invitation_email(
    *,
    to_email: str,
    to_name: str,
    inviter_name: str,
    role_names: list[str],
    raw_token: str,
    note: str | None = None,
) -> None:
    """Send the invitation. Raises if email is not configured.

    Raising rather than returning False is deliberate: the caller must not be
    able to record an invitation as sent when nothing left the building. The
    brief is explicit about not pretending.
    """
    from app.services.email_service import (
        _cta,
        _detail_panel,
        _fine,
        _greeting_name,
        _h1,
        _p,
        _shell,
        _strip_html_to_text,
        send_transactional_email,
    )

    if not settings.brevo_is_configured:
        raise RuntimeError(
            "Email sending is not configured, so the invitation cannot be delivered."
        )

    url = acceptance_url(raw_token)
    roles_line = ", ".join(role_names) if role_names else "No roles yet"

    rows = [("Your access", roles_line), ("Invited by", inviter_name)]
    if note:
        rows.append(("Note", note))

    body = (
        _h1("You've been invited to administer ODOS")
        + _p(f"{_greeting_name(to_name)}, you've been given an ODOS administrator account.")
        + _detail_panel(rows)
        + _p("Choose a password to finish setting up your account:")
        + _cta("Accept the invitation", url)
        + _fine(
            f"This link works once and expires in {INVITATION_TTL_HOURS} hours. "
            "If you weren't expecting this, you can ignore this email — no account "
            "exists until the link is used.",
            last=True,
        )
    )
    html = _shell(
        preheader="Set up your ODOS administrator account",
        body=body,
        audience_line="You received this because an ODOS administrator invited you.",
    )

    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject="You've been invited to administer ODOS",
        html_content=html,
        text_content=_strip_html_to_text(html),
    )
