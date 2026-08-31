"""add invoice.transaction_status / classification_source / amount_mentioned,
and the InvoiceStatus.rejected state

Revision ID: 20260902_01
Revises: 20260901_01
Create Date: 2026-09-02

Non-transactional document handling (docs/invoice-ocr-plan.md §12). Internal
paperwork — a minute sheet, an approval request — carries amounts but is not
a purchase, and was previously pushed through purchase-invoice extraction
and then shown as "Uncategorized", losing what the document actually is.

Three deliberate choices recorded here because they shape the data:

1. `transaction_status` is a *separate axis* from `status`, not another
   value inside it. `status` owns the human workflow (needs_review ->
   validated -> sent_to_accounting / rejected); this owns "does this belong
   in financial processing at all". Keeping them apart avoids duplicating
   the review and rejected concepts that already exist.
2. Both new enum columns are NOT NULL with a server_default, so every
   existing row becomes `transactional` / `rule` — i.e. behaves exactly as
   it did before this migration. No row is reclassified retroactively.
3. `amount_mentioned` is a separate column from `total` on purpose: a
   non-transactional document has no transaction total, and letting its
   stated amount sit in `total` is precisely how an internal memo would
   become a booked expense (every spend aggregate, cashbook total and
   accounting hand-off reads `total`).

`rejected` is added to the existing invoice_status enum rather than
hard-deleting rows: the source file, extraction and audit trail all stay
intact, and the decision is reversible.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260902_01"
down_revision = "20260901_01"
branch_labels = None
depends_on = None

_TRANSACTION_STATUS = sa.Enum("transactional", "non_transactional", name="invoice_transaction_status")
_CLASSIFICATION_SOURCE = sa.Enum("rule", "user_override", name="invoice_classification_source")


def upgrade() -> None:
    bind = op.get_bind()
    _TRANSACTION_STATUS.create(bind, checkfirst=True)
    _CLASSIFICATION_SOURCE.create(bind, checkfirst=True)

    op.add_column(
        "invoice",
        sa.Column(
            "transaction_status", _TRANSACTION_STATUS,
            nullable=False, server_default="transactional",
        ),
    )
    op.add_column(
        "invoice",
        sa.Column(
            "classification_source", _CLASSIFICATION_SOURCE,
            nullable=False, server_default="rule",
        ),
    )
    op.add_column("invoice", sa.Column("amount_mentioned", sa.Float(), nullable=True))
    # Every listing of the non-transactional area filters on exactly this
    # pair, the same company-scoped shape ix_invoice_company_category uses.
    op.create_index("ix_invoice_company_transaction_status", "invoice", ["company_id", "transaction_status"])

    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    # PostgreSQL, and is a no-op on SQLite (used by the test suite, where
    # enums are plain VARCHAR + CHECK), hence the dialect guard.
    if bind.dialect.name == "postgresql":
        op.execute("COMMIT")
        op.execute("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    op.drop_index("ix_invoice_company_transaction_status", table_name="invoice")
    op.drop_column("invoice", "amount_mentioned")
    op.drop_column("invoice", "classification_source")
    op.drop_column("invoice", "transaction_status")
    bind = op.get_bind()
    _CLASSIFICATION_SOURCE.drop(bind, checkfirst=True)
    _TRANSACTION_STATUS.drop(bind, checkfirst=True)
    # PostgreSQL cannot remove a value from an enum type; 'rejected' is left
    # in place deliberately rather than rebuilding invoice_status here — an
    # unused extra value is harmless, a type rebuild during a downgrade is
    # not.
