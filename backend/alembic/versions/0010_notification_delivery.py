"""Track per-user notification reads and email delivery attempts.

Revision ID: 0010_notification_delivery
Revises: 0009_expense_workflow
"""
from __future__ import annotations

from alembic import op

revision = "0010_notification_delivery"
down_revision = "0009_expense_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_deliveries (
          notification_id BIGINT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
          user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          read_at TIMESTAMPTZ,
          email_attempts INT NOT NULL DEFAULT 0 CHECK (email_attempts >= 0),
          next_email_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          email_sent_at TIMESTAMPTZ,
          email_last_error TEXT,
          PRIMARY KEY (notification_id, user_id)
        );
        CREATE INDEX notification_delivery_user_ix
          ON notification_deliveries (user_id, read_at, notification_id DESC);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX notification_delivery_user_ix;
        DROP TABLE notification_deliveries;
        """
    )
