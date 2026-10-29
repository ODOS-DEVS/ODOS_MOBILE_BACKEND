"""Create (or promote) an admin account.

No migration seeds a user, so after a full database reset there is nobody who
can sign in — including to the admin dashboard that would show whether the
reset worked. This is the way back in.

Safe to re-run. Given an email that already exists, it promotes that account
to admin and resets its password rather than failing on the unique
constraint, which is what you want when locked out of an account you own.

Usage:
    ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='...' \\
    ADMIN_NAME='Your Name' \\
    .venv/bin/python scripts/create_admin.py

Run it inside the deployed container, where DATABASE_URL already points at
production. The password is read from the environment and never written to
the database in plaintext or echoed back.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import User, UserRole, VendorStatus


def main() -> int:
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    full_name = (os.environ.get("ADMIN_NAME") or "ODOS Admin").strip()

    if not email or not password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD are required.", file=sys.stderr)
        return 2
    if len(password) < 10:
        print("Use a password of at least 10 characters.", file=sys.stderr)
        return 2

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    # The app pins psycopg3; a bare postgresql:// URL would select psycopg2.
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = sa.create_engine(database_url, future=True)
    with Session(engine) as db:
        existing = db.scalar(sa.select(User).where(sa.func.lower(User.email) == email))

        if existing:
            existing.role = UserRole.ADMIN
            existing.hashed_password = hash_password(password)
            existing.is_active = True
            existing.is_verified = True
            # Invalidate any token minted before this reset: if the account
            # was locked because someone else held a session, promoting it
            # without bumping this would leave that session valid.
            existing.token_version = (existing.token_version or 0) + 1
            existing.updated_at = datetime.now(UTC)
            db.commit()
            print(f"Promoted existing account to admin: {email}")
            return 0

        db.add(
            User(
                id=uuid.uuid4(),
                full_name=full_name,
                email=email,
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
                vendor_status=VendorStatus.NONE,
                is_active=True,
                is_verified=True,
                phone_verified=False,
                token_version=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        db.commit()
        print(f"Created admin: {email}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
