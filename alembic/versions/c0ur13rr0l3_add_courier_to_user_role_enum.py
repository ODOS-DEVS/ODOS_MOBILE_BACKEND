"""Add 'courier' to the user_role Postgres enum

Revision ID: c0ur13rr0l3
Revises: c0ur13rf13et

app/models/user.py already gained UserRole.COURIER, but adding a value to the
Python enum does not touch the database's own user_role type -- that's a
native Postgres enum, not a plain string column, and needs its own ALTER TYPE.
Caught by the concurrent-claim integration test: inserting a test courier user
failed with "invalid input value for enum user_role: courier" against a
database that had never been told the value exists.

ALTER TYPE ... ADD VALUE is safe inside Alembic's transactional DDL on
Postgres 12+, provided the new value is not also used within the same
migration -- which this one doesn't.
"""

from alembic import op

revision = "c0ur13rr0l3"
down_revision = "c0ur13rf13et"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumtypid = 'user_role'::regtype AND enumlabel = 'courier'
            ) THEN
                ALTER TYPE user_role ADD VALUE 'courier';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums. Removing a value cleanly requires
    # rebuilding the type and every column that uses it, which is more
    # disruptive than this migration's forward change justifies rolling back
    # in isolation.
    pass
