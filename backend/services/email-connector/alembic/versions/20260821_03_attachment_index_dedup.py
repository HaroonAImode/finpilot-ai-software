"""add email_attachment.attachment_index and dedup on it instead of Gmail's
ephemeral attachmentId

Gmail regenerates attachmentId on every messages.get call for the same
unchanged attachment, so keying dedup on it made every re-sync insert a
complete duplicate set of rows (found live: one unchanged file accumulated
12 distinct ids across 12 runs). Position within the message's MIME tree is
stable — a received message is immutable — so that becomes the key.

Revision ID: 20260821_03
Revises: 20260821_02
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "20260821_03"
down_revision = "20260821_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_attachment",
        sa.Column("attachment_index", sa.Integer(), nullable=False, server_default="0"),
    )

    # Existing rows were written under the broken key, so a message with N
    # attachments may hold several duplicate sets, all with attachment_index
    # 0 and no way to tell which position each row was meant to be. Rather
    # than guess, keep one row per (message, filename) — preferring a
    # successfully downloaded one, then the newest — and drop the rest. The
    # next sync re-creates anything genuinely missing with a correct index,
    # so this loses nothing that cannot be rebuilt.
    op.execute(
        """
        DELETE FROM email_attachment a
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY message_id, filename
                       ORDER BY downloaded DESC, created_at DESC
                   ) AS rn
            FROM email_attachment
        ) ranked
        WHERE a.id = ranked.id AND ranked.rn > 1
        """
    )

    # Renumber the survivors by filename so each row within a message gets a
    # distinct index, keeping the new unique constraint satisfiable.
    op.execute(
        """
        UPDATE email_attachment a
        SET attachment_index = ranked.rn - 1
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY filename, created_at) AS rn
            FROM email_attachment
        ) ranked
        WHERE a.id = ranked.id
        """
    )

    op.create_unique_constraint(
        "uq_email_attachment_message_index", "email_attachment", ["message_id", "attachment_index"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_email_attachment_message_index", "email_attachment", type_="unique")
    op.drop_column("email_attachment", "attachment_index")
