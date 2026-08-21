"""add assistant conversations, messages, and feedback

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-07-06 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use IF NOT EXISTS so Render can recover when tables already exist but
    # alembic_version was stamped behind this revision.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS assistant_conversations (
                id UUID NOT NULL,
                user_id UUID NOT NULL,
                screen VARCHAR(80),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_user_id "
            "ON assistant_conversations (user_id)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS assistant_messages (
                id UUID NOT NULL,
                conversation_id UUID NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                metadata_json JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY (conversation_id)
                    REFERENCES assistant_conversations (id) ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_assistant_messages_conversation_id "
            "ON assistant_messages (conversation_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_assistant_messages_created_at "
            "ON assistant_messages (created_at)"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS assistant_message_feedback (
                id UUID NOT NULL,
                message_id UUID NOT NULL,
                user_id UUID NOT NULL,
                rating INTEGER NOT NULL,
                comment VARCHAR(500),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY (message_id) REFERENCES assistant_messages (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                CONSTRAINT uq_assistant_feedback_message_user UNIQUE (message_id, user_id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_assistant_message_feedback_message_id "
            "ON assistant_message_feedback (message_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_assistant_message_feedback_user_id "
            "ON assistant_message_feedback (user_id)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS assistant_message_feedback CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS assistant_messages CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS assistant_conversations CASCADE"))
