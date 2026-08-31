"""Invoice CRUD — architecture report §5.3's second responsibility (the
first, the scanner, lives in scanner.py). List/filter, view, correct after
review, and the two lifecycle transitions (validate, send-to-accounting).
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import (
    SUGGESTED_CATEGORIES, ClassificationSource, Invoice, InvoiceItem, InvoiceStatus, InvoiceType,
    PaymentMethod, TransactionStatus,
)
from app.schemas.invoice import (
    BulkLinkVendorRequest, BulkLinkVendorResponse, CategorySummary, InvoiceListItem, InvoiceListResponse,
    InvoiceOptions, InvoiceResponse, InvoiceUpdateRequest, MonthlyPaymentSummary, ReclassifyRequest,
    UnlinkedVendorGroup, VendorSpend,
)
from app.services.ai_engine_client import AIEngineError, render_invoice_page
from app.services.category_report_pdf import render_category_report_pdf
from app.services.settings_service_client import SettingsServiceError, fetch_company_branding
from app.services.storage import StorageManager
from app.services.transactions_service_client import TransactionsServiceError, book_expense_from_invoice

#: The two statuses that count as "in the cashbook" — the same pair Saved
#: Records' own list query has always used. Category aggregation and the
#: category PDF report both default to this so their totals mean the same
#: thing as what the page already shows.
_CASHBOOK_STATUSES = (InvoiceStatus.validated, InvoiceStatus.sent_to_accounting)

#: The `?category=` value the summary/report endpoints accept to mean "no
#: category set" — a URL query param cannot carry NULL. Deliberately absent
#: from InvoiceOptions.categories: that list is for *assigning* a category,
#: and letting someone assign the literal string "Uncategorized" would be
#: indistinguishable from actually clearing the field.
UNCATEGORIZED = "Uncategorized"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _get_invoice_scoped(db: AsyncSession, invoice_id: UUID, company_id: UUID) -> Invoice:
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        .options(selectinload(Invoice.items))
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/", response_model=InvoiceListResponse)
async def list_invoices(
    status: Optional[str] = Query(
        None, description="Filter by status, or a comma-separated list (e.g. 'validated,sent_to_accounting'), or omit for all",
    ),
    type: Optional[str] = Query(
        "purchase",
        description="Filter by invoice type ('purchase' or 'sale'), or 'all' for both. "
                    "Defaults to purchase so generated sales invoices never appear in the "
                    "Scanner's own list, which is about documents that were received.",
    ),
    vendor_id: Optional[UUID] = Query(
        None, description="Only invoices linked to this Vendors Service record.",
    ),
    transaction_status: Optional[str] = Query(
        None,
        description=(
            "Filter by 'transactional' or 'non_transactional', or omit for both. "
            "Non-transactional documents (a minute sheet, an approval request) carry amounts "
            "but are not purchases — this is what the Non-Transactional Documents area lists."
        ),
    ),
    date_from: Optional[date] = Query(
        None, description="Inclusive lower bound on invoice_date — Saved Records' month filter.",
    ),
    date_to: Optional[date] = Query(
        None, description="Inclusive upper bound on invoice_date — Saved Records' month filter.",
    ),
    skip: int = Query(0, ge=0),
    # le=1000, not 100: Saved Records fetches up to 500 rows in one page for
    # its category-grouped display (totals still always come from
    # GET /invoices/categories/summary, never a count of this list — see
    # that endpoint's own docstring). 100 was fine for the Scanner's own
    # paginated queue; it is not fine for "show me this whole cashbook."
    limit: int = Query(20, ge=1, le=1000),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    query = select(Invoice).where(Invoice.company_id == company_id)
    if type and type != "all":
        try:
            query = query.where(Invoice.type == InvoiceType(type))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown invoice type '{type}'")
    # A row with no invoice_date at all is excluded once *either* bound is
    # given — same convention vendor_spend's own date filter uses: a month
    # filter cannot honestly place an undated row in the month it claims to
    # show.
    if date_from is not None or date_to is not None:
        query = query.where(Invoice.invoice_date.isnot(None))
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)
    if vendor_id is not None:
        query = query.where(Invoice.vendor_id == vendor_id)
    if transaction_status:
        try:
            query = query.where(Invoice.transaction_status == TransactionStatus(transaction_status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown transaction status '{transaction_status}'")
    if status:
        try:
            statuses = [InvoiceStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
        query = query.where(Invoice.status.in_(statuses))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return InvoiceListResponse(
        invoices=[InvoiceListItem.model_validate(row) for row in rows], total=total or 0, skip=skip, limit=limit,
    )


@router.get("/vendor-spend", response_model=list[VendorSpend])
async def vendor_spend(
    date_from: Optional[date] = Query(
        None, description="Inclusive lower bound on invoice_date. Omit for no lower bound.",
    ),
    date_to: Optional[date] = Query(
        None, description="Inclusive upper bound on invoice_date. Omit for no upper bound.",
    ),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> list[VendorSpend]:
    """Total purchase spend per vendor, aggregated in one query.

    Exists so Vendors Service can derive `total_spend_pkr` without asking
    per vendor — that would be an N+1 across a service boundary, which is
    exactly the cost that makes people cache an aggregate and then let it
    drift. One call keeps the figure derived and correct.

    Grouped by `vendor_id`, so only invoices actually linked to a vendor
    record are counted. `vendor_name` comes along for display, taking the
    most recent invoice's spelling — earlier ones may be OCR misreads of
    the same vendor, which is the whole reason vendor_id exists.

    Sales invoices are excluded: money billed *out* is not spend.

    `date_from`/`date_to` are independently optional — added for Reports
    Service's Purchase Report, which needs a spend total scoped to a
    specific period rather than Vendors Service's original all-time use.
    A row with no `invoice_date` at all is excluded once *either* bound is
    given: a report cannot honestly count spend in a period it cannot
    place in time.
    """
    query = select(
        Invoice.vendor_id,
        func.max(Invoice.vendor_name).label("vendor_name"),
        func.coalesce(func.sum(Invoice.total), 0.0).label("total_spend"),
        func.count().label("invoice_count"),
    ).where(
        Invoice.company_id == company_id, Invoice.type == InvoiceType.purchase, Invoice.vendor_id.isnot(None),
    )
    if date_from is not None or date_to is not None:
        query = query.where(Invoice.invoice_date.isnot(None))
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)

    rows = (await db.execute(query.group_by(Invoice.vendor_id))).all()

    return [
        VendorSpend(
            vendor_id=row.vendor_id, vendor_name=row.vendor_name,
            total_spend=float(row.total_spend or 0.0), invoice_count=row.invoice_count,
        )
        for row in rows
    ]


@router.get("/vendor-groups", response_model=list[UnlinkedVendorGroup])
async def unlinked_vendor_groups(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> list[UnlinkedVendorGroup]:
    """Every distinct raw `vendor_name` on a purchase invoice with no
    `vendor_id` yet, one row per name with every invoice that carries it.

    For Vendors Service's reconciliation queue (§3.8): grouping by the exact
    string here — not fuzzy — because an accountant clearing "INVOICE
    Invoice #: INV-2025-0872" off three invoices at once is the whole point;
    fuzzy grouping across *different* raw strings is Vendors Service's job,
    since it alone knows the vendor directory to fuzzy-match against.

    Blank/null vendor_name is excluded — there is no string to review, and
    linking those needs a human to read the source document, not a queue.

    Grouped in Python rather than with `array_agg`: that function is
    Postgres-only, and this endpoint's own test suite runs on SQLite. Row
    counts here are bounded by "invoices nobody has reconciled yet", not by
    the whole ledger, so there is no volume this would struggle with.
    """
    rows = (
        await db.execute(
            select(Invoice.id, Invoice.vendor_name, Invoice.total, Invoice.invoice_number, Invoice.invoice_date)
            .where(
                Invoice.company_id == company_id,
                Invoice.type == InvoiceType.purchase,
                Invoice.vendor_id.is_(None),
                Invoice.vendor_name.isnot(None),
                func.trim(Invoice.vendor_name) != "",
            )
        )
    ).all()

    groups: dict[str, UnlinkedVendorGroup] = {}
    for row in rows:
        group = groups.get(row.vendor_name)
        if group is None:
            group = UnlinkedVendorGroup(
                vendor_name=row.vendor_name, invoice_ids=[], invoice_count=0, total_amount=0.0,
                sample_invoice_number=row.invoice_number, latest_invoice_date=row.invoice_date,
            )
            groups[row.vendor_name] = group
        group.invoice_ids.append(row.id)
        group.invoice_count += 1
        group.total_amount += row.total or 0.0
        if row.invoice_date and (group.latest_invoice_date is None or row.invoice_date > group.latest_invoice_date):
            group.latest_invoice_date = row.invoice_date
            group.sample_invoice_number = row.invoice_number

    return sorted(groups.values(), key=lambda g: g.invoice_count, reverse=True)


@router.post("/bulk-link-vendor", response_model=BulkLinkVendorResponse)
async def bulk_link_vendor(
    payload: BulkLinkVendorRequest,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> BulkLinkVendorResponse:
    """Sets `vendor_id` on every listed invoice in one transaction — the
    reconciliation queue's "link" action applies to a whole name-group at
    once, not one PUT per invoice.

    Silently ignores ids that do not belong to this company or are not
    purchase invoices, rather than 404ing the whole batch over one bad id:
    the caller already scoped the ids from this company's own vendor-groups
    response, so a mismatch here means something else changed the invoice
    between the two calls, not a client bug worth failing loudly for.
    """
    result = await db.execute(
        select(Invoice).where(
            Invoice.id.in_(payload.invoice_ids),
            Invoice.company_id == company_id,
            Invoice.type == InvoiceType.purchase,
        )
    )
    invoices = result.scalars().all()
    for invoice in invoices:
        invoice.vendor_id = payload.vendor_id
    await db.commit()

    logger.info(
        "Bulk-linked invoices to vendor",
        extra={"vendor_id": str(payload.vendor_id), "count": len(invoices)},
    )
    return BulkLinkVendorResponse(updated_count=len(invoices))


@router.get("/status-summary")
async def status_summary(
    type: Optional[str] = Query(
        "purchase",
        description="Filter by invoice type ('purchase' or 'sale'), or 'all' for both. "
                    "Defaults to purchase — architecture report §17's dashboard panel is "
                    "about received documents, same default as the list endpoint above.",
    ),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Document counts by status — the Dashboard's Invoice Status chart
    (architecture report §17). One grouped COUNT rather than one call per
    status, so the panel is a single round trip regardless of how many
    statuses exist.
    """
    query = select(Invoice.status, func.count()).where(Invoice.company_id == company_id)
    if type and type != "all":
        try:
            query = query.where(Invoice.type == InvoiceType(type))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown invoice type '{type}'")
    rows = (await db.execute(query.group_by(Invoice.status))).all()

    counts = {status.value: 0 for status in InvoiceStatus}
    for status, count in rows:
        counts[status.value] = count
    return {"counts": counts, "total": sum(counts.values())}


@router.get("/options", response_model=InvoiceOptions)
async def invoice_options() -> InvoiceOptions:
    """Suggested cashbook categories for Saved Records — same "suggestion
    list" endpoint shape as every other SUGGESTED_* options route in this
    codebase (HR Service, Settings, Vendors)."""
    return InvoiceOptions(categories=list(SUGGESTED_CATEGORIES))


async def _category_summary_rows(
    db: AsyncSession, company_id: UUID, *, date_from: Optional[date] = None, date_to: Optional[date] = None,
) -> list[CategorySummary]:
    """The one query every category/grand total on Saved Records and the
    category PDF report both read from (docs/superpowers/specs/
    2026-09-01-saved-records-cashbook-design.md §7) — a single SQL
    GROUP BY over the full matching row set, never a client-side re-sum of
    whatever page happened to be fetched. `func.coalesce(..., 0.0)` covers a
    category with rows but no `total` set yet (still needs-review data
    should never reach this far, but a defensive 0 beats a NULL total).

    `date_from`/`date_to` back the History month picker — same "undated
    rows excluded once either bound is given" rule as list_invoices' own
    date filter, so a month total can never silently include a row that
    cannot actually be placed in that month.

    `transaction_status == transactional` is required for the same reason
    docs/invoice-ocr-plan.md §12 requires it everywhere else a `total` is
    read: a confirmed non-transactional document (transaction_status=
    non_transactional, status=validated — see Invoice.transaction_status'
    own docstring) has `category=None` and `total=None` by design, but
    without this filter it would still count as a real row in the
    Uncategorized bucket the moment it's confirmed — inflating that
    category's count even though its total contributes nothing. Found live
    during the Cash Book audit; not previously exercised because no test
    seeded a validated non-transactional row.
    """
    query = (
        select(
            Invoice.category,
            func.count().label("count"),
            func.coalesce(func.sum(Invoice.total), 0.0).label("total"),
        )
        .where(
            Invoice.company_id == company_id, Invoice.type == InvoiceType.purchase,
            Invoice.status.in_(_CASHBOOK_STATUSES), Invoice.transaction_status == TransactionStatus.transactional,
        )
    )
    if date_from is not None or date_to is not None:
        query = query.where(Invoice.invoice_date.isnot(None))
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)

    rows = (await db.execute(query.group_by(Invoice.category))).all()
    summaries = [
        CategorySummary(category=row.category, count=row.count, total=float(row.total or 0.0)) for row in rows
    ]
    # Uncategorized (category IS NULL) last, everything else alphabetically —
    # matches how the left pane orders its category sections.
    summaries.sort(key=lambda s: (s.category is None, (s.category or "").lower()))
    return summaries


@router.get("/categories/summary", response_model=list[CategorySummary])
async def categories_summary(
    date_from: Optional[date] = Query(None, description="Inclusive lower bound — Saved Records' month filter."),
    date_to: Optional[date] = Query(None, description="Inclusive upper bound — Saved Records' month filter."),
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> list[CategorySummary]:
    """Accurate per-category totals for Saved Records' cashbook view — see
    _category_summary_rows above. Scoped to purchase invoices that are
    validated or sent to accounting, the same two statuses the page's own
    list query has always used."""
    return await _category_summary_rows(db, company_id, date_from=date_from, date_to=date_to)


@router.get("/monthly-summary", response_model=MonthlyPaymentSummary)
async def monthly_summary(
    date_from: Optional[date] = Query(None, description="Inclusive lower bound — Saved Records' month filter."),
    date_to: Optional[date] = Query(None, description="Inclusive upper bound — Saved Records' month filter."),
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> MonthlyPaymentSummary:
    """Saved Records' Cash Book summary strip — one SQL GROUP BY over
    `payment_method`, the same "backend is the source of truth, never a
    client-side re-sum" discipline _category_summary_rows already uses for
    categories. Scoped identically: purchase invoices, validated or sent
    to accounting, and — critically — transactional only, so a confirmed
    non-transactional document's absent total (§12) can never appear as a
    real zero-value row skewing `transaction_count`.
    """
    query = (
        select(Invoice.payment_method, func.count().label("count"), func.coalesce(func.sum(Invoice.total), 0.0).label("total"))
        .where(
            Invoice.company_id == company_id, Invoice.type == InvoiceType.purchase,
            Invoice.status.in_(_CASHBOOK_STATUSES), Invoice.transaction_status == TransactionStatus.transactional,
        )
    )
    if date_from is not None or date_to is not None:
        query = query.where(Invoice.invoice_date.isnot(None))
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)

    rows = (await db.execute(query.group_by(Invoice.payment_method))).all()

    cash_total = online_total = unspecified_total = 0.0
    transaction_count = 0
    for payment_method, count, total in rows:
        transaction_count += count
        amount = float(total or 0.0)
        if payment_method == PaymentMethod.cash:
            cash_total += amount
        elif payment_method == PaymentMethod.bank:
            online_total += amount
        else:
            unspecified_total += amount

    return MonthlyPaymentSummary(
        monthly_total=cash_total + online_total + unspecified_total,
        cash_total=cash_total, online_total=online_total, unspecified_total=unspecified_total,
        transaction_count=transaction_count,
    )


@router.get("/categories/available-months", response_model=list[str])
async def available_months(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Every "YYYY-MM" with at least one saved purchase invoice — Saved
    Records' History picker offers exactly these months, never a blind
    calendar that could land on one with nothing in it. Grouped in Python,
    not a SQL date_trunc/to_char: this endpoint's own test suite runs on
    SQLite, which has neither — same reasoning unlinked_vendor_groups'
    own grouping already documents.

    `transaction_status == transactional` for the same reason
    _category_summary_rows requires it: a confirmed non-transactional
    document has its own real invoice_date, and without this filter a
    month containing only such a document would wrongly appear as if it
    had real cashbook activity.
    """
    dates = (
        await db.execute(
            select(Invoice.invoice_date).where(
                Invoice.company_id == company_id, Invoice.type == InvoiceType.purchase,
                Invoice.status.in_(_CASHBOOK_STATUSES), Invoice.invoice_date.isnot(None),
                Invoice.transaction_status == TransactionStatus.transactional,
            )
        )
    ).scalars().all()
    return sorted({d.strftime("%Y-%m") for d in dates}, reverse=True)


@router.get("/categories/report.pdf")
async def category_report_pdf(
    category: Optional[str] = Query(
        None,
        description=f"A category name, or '{UNCATEGORIZED}' for invoices with no category set. "
                    "Omit for a single report covering every category.",
    ),
    date_from: Optional[date] = Query(None, description="Inclusive lower bound — Saved Records' month filter."),
    date_to: Optional[date] = Query(None, description="Inclusive upper bound — Saved Records' month filter."),
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """A company-branded PDF: this category's entries as a table with an
    accurate totals row (from the same aggregate query the on-screen total
    uses — see _category_summary_rows), followed by one labeled appendix
    page per entry showing its actual receipt/invoice image. Best-effort
    per entry: a source file that fails to load is noted and skipped rather
    than failing the whole report over one bad file.
    """
    query = select(Invoice).where(
        Invoice.company_id == company_id, Invoice.type == InvoiceType.purchase,
        Invoice.status.in_(_CASHBOOK_STATUSES),
    )
    if category == UNCATEGORIZED:
        query = query.where(Invoice.category.is_(None))
    elif category:
        query = query.where(Invoice.category == category)
    if date_from is not None or date_to is not None:
        query = query.where(Invoice.invoice_date.isnot(None))
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)
    invoices = (await db.execute(query.order_by(Invoice.invoice_date, Invoice.created_at))).scalars().all()

    company_name = None
    logo_data_uri = None
    try:
        branding = await fetch_company_branding(settings, company_id)
        company_name = branding.get("name")
        logo_data_uri = branding.get("logo_url")
    except SettingsServiceError as exc:
        logger.warning("Could not fetch company branding for category report, company %s: %s", company_id, exc)

    storage = StorageManager(settings)
    entry_images: dict[UUID, bytes] = {}
    for invoice in invoices:
        if not invoice.s3_key:
            continue
        try:
            s3_object = storage.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=invoice.s3_key)
            raw = s3_object["Body"].read()
        except Exception:
            logger.warning("Could not load source file for invoice %s in category report", invoice.id)
            continue
        if invoice.mimetype and invoice.mimetype.startswith("image/"):
            entry_images[invoice.id] = raw
        else:
            try:
                entry_images[invoice.id] = await render_invoice_page(
                    settings, filename=invoice.filename or "document", content=raw,
                    mimetype=invoice.mimetype, page=0,
                )
            except AIEngineError as exc:
                logger.warning("Could not render page image for invoice %s in category report: %s", invoice.id, exc)

    # The totals row must equal the on-screen total exactly — computed via
    # the same aggregate query, never a Python sum() over `invoices` above
    # (raw, unaggregated rows), even though today they would agree: one
    # query is the single source of truth this whole feature promises (§7
    # of the design spec). The "every category" report's total is the sum
    # of those same accurate per-category aggregates — still zero re-
    # derivation from raw row data, just addition over already-authoritative
    # numbers.
    summary_rows = await _category_summary_rows(db, company_id, date_from=date_from, date_to=date_to)
    if category == UNCATEGORIZED:
        matching = next((s for s in summary_rows if s.category is None), None)
        category_total = matching.total if matching else 0.0
    elif category:
        matching = next((s for s in summary_rows if s.category == category), None)
        category_total = matching.total if matching else 0.0
    else:
        category_total = sum(s.total for s in summary_rows)

    pdf_bytes = render_category_report_pdf(
        category_label=category or "All Categories",
        invoices=invoices, entry_images=entry_images, category_total=category_total, company_name=company_name,
        logo_data_uri=logo_data_uri,
    )
    safe_label = (category or "all-categories").replace("/", "-").replace(" ", "-").lower()
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_label}-report.pdf"'},
    )


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    return InvoiceResponse.model_validate(invoice)


@router.get("/{invoice_id}/content")
async def get_invoice_content(
    invoice_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Streams the original uploaded file — the review UI's "show exactly
    what was extracted from where" view (Rule 8.2) needs the source
    document alongside the structured fields, not just the numbers alone.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    storage = StorageManager(settings)
    try:
        s3_object = storage.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=invoice.s3_key)
    except Exception:
        logger.exception("Failed to open invoice %s from object storage", invoice_id)
        raise HTTPException(status_code=502, detail="Invoice file storage is temporarily unavailable")

    safe_filename = invoice.filename.replace('"', "'").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        iter(lambda: s3_object["Body"].read(1024 * 1024), b""),
        media_type=invoice.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@router.get("/{invoice_id}/page/{page_number}")
async def get_invoice_page(
    invoice_id: UUID, page_number: int,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Rasterizes one page of the original document — the bounding-box
    review UI's document image (the second scanner experience). Proxies to
    AI Engine's stateless render endpoint rather than doing PyMuPDF here,
    same reasoning as the scanner already calling out to AI Engine for
    extraction instead of embedding OCR logic in this service.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    storage = StorageManager(settings)
    try:
        s3_object = storage.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=invoice.s3_key)
        content = s3_object["Body"].read()
    except Exception:
        logger.exception("Failed to open invoice %s from object storage", invoice_id)
        raise HTTPException(status_code=502, detail="Invoice file storage is temporarily unavailable")

    try:
        png_bytes = await render_invoice_page(
            settings, filename=invoice.filename, content=content, mimetype=invoice.mimetype, page=page_number,
        )
    except AIEngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=png_bytes, media_type="image/png")


@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: UUID, payload: InvoiceUpdateRequest,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """A human's correction after review. Only fields actually present in
    the request are applied (model_fields_set) — an omitted field is left
    alone, matching the convention used across this codebase's other PATCH
    /PUT endpoints. `raw_extraction_json` is never touched: Rule 8.1 —
    the original extraction stays intact for audit even after a correction.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)

    fields = payload.model_dump(exclude_unset=True, exclude={"items"})

    # Status is editable only *between* the two saved states. Reaching them in
    # the first place still requires the lifecycle endpoints, so this cannot be
    # used to promote an invoice that was never reviewed — the same guard
    # POST /send-to-accounting enforces, applied here too rather than left as a
    # hole in the grid's PUT.
    if "status" in fields and fields["status"] is not None:
        if invoice.status not in (InvoiceStatus.validated, InvoiceStatus.sent_to_accounting):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This invoice has not been validated yet, so its status cannot be set here. "
                    "Validate it from the Scanner first."
                ),
            )
        fields["status"] = InvoiceStatus(fields["status"])

    for field_name, value in fields.items():
        if field_name == "payment_method" and value is not None:
            value = PaymentMethod(value)
        setattr(invoice, field_name, value)

    if "items" in payload.model_fields_set and payload.items is not None:
        for existing_item in list(invoice.items):
            await db.delete(existing_item)
        await db.flush()
        invoice.items = [
            InvoiceItem(
                position=i, description=item.description, qty=item.qty, rate=item.rate, amount=item.amount,
                arithmetic_check="not_checked", review_flags=[],
            )
            for i, item in enumerate(payload.items)
        ]

    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])
    return InvoiceResponse.model_validate(invoice)


@router.post("/{invoice_id}/validate", response_model=InvoiceResponse)
async def validate_invoice(
    invoice_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """A human confirmed the (possibly corrected) data is right.

    This is also the "Confirm" action on a non-transactional document
    ("yes, this really is a minute sheet") — deliberately the same endpoint
    rather than a second one, since it means exactly the same thing: a
    person reviewed what the system decided and agreed with it. The
    document keeps transaction_status=non_transactional and simply stops
    being an open review item.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    invoice.status = InvoiceStatus.validated
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])
    return InvoiceResponse.model_validate(invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceResponse)
async def reject_invoice(
    invoice_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """A human decided this document should not be processed at all.

    A soft state, deliberately not a row delete: the uploaded file, the
    extraction and Rule 8.1's audit trail all stay intact, and the decision
    is reversible (POST /reclassify brings it back into processing). This
    service has no delete endpoint at all — permanent deletion, if it is
    ever wanted, is a separate explicit action, not a side effect of a
    reviewer clicking Reject.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    invoice.status = InvoiceStatus.rejected
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])
    return InvoiceResponse.model_validate(invoice)


@router.post("/{invoice_id}/reclassify", response_model=InvoiceResponse)
async def reclassify_invoice(
    invoice_id: UUID, payload: ReclassifyRequest,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    """A human disagreeing with the system's transactional/non-transactional
    call — the "Process as Transaction" action, and its reverse.

    The rules engine's original verdict is never overwritten: it stays in
    `raw_extraction_json` (Rule 8.1) exactly as it was, and
    `classification_source` flips to `user_override` so the disagreement is
    visible rather than silent. That is what makes this auditable and what
    lets the deterministic rules be improved against real corrections later.

    Promoting a non-transactional document to transactional moves its
    `amount_mentioned` into `total` when no total was extracted — that
    amount was only ever held apart *because* the document wasn't a
    transaction, and once a person says it is, the figure they already saw
    on screen is the one that should be there. It is not invented: if the
    document stated no amount, `total` stays empty for a human to fill in.
    Demoting does the reverse, so no transaction total is left behind on a
    document that is no longer one.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    target = TransactionStatus(payload.transaction_status)

    if target is TransactionStatus.transactional and invoice.transaction_status is not target:
        if invoice.total is None and invoice.amount_mentioned is not None:
            invoice.total = invoice.amount_mentioned
            invoice.amount_mentioned = None
    elif target is TransactionStatus.non_transactional and invoice.transaction_status is not target:
        if invoice.amount_mentioned is None and invoice.total is not None:
            invoice.amount_mentioned = invoice.total
            invoice.total = None
        # A cashbook category is meaningless on a document that isn't a
        # purchase — clearing it here is what keeps the non-transactional
        # area from being a second, confusing home for cashbook rows.
        invoice.category = None

    invoice.transaction_status = target
    invoice.classification_source = ClassificationSource.user_override
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])
    return InvoiceResponse.model_validate(invoice)


@router.post("/{invoice_id}/send-to-accounting", response_model=InvoiceResponse)
async def send_to_accounting(
    invoice_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvoiceResponse:
    """Books the invoice.

    For a purchase invoice, this now really is the hand-off the old
    docstring here promised: a matching Expense record is booked in
    Transactions Service (§5.4), best-effort — see
    transactions_service_client.py for why a failure there does not block
    this status flip. For a sale, Revenue Manager already treats this
    service's own scanned-sales summary as the revenue ledger (deliberately
    not duplicated into a second Transactions-side table — see the
    Transactions Service design spec §on scope), so no second call is made.
    """
    invoice = await _get_invoice_scoped(db, invoice_id, company_id)
    if invoice.status not in (InvoiceStatus.processed, InvoiceStatus.validated):
        raise HTTPException(
            status_code=409,
            detail="This invoice needs review or validation before it can be sent to accounting.",
        )
    invoice.status = InvoiceStatus.sent_to_accounting
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])

    if invoice.type is InvoiceType.purchase:
        try:
            await book_expense_from_invoice(
                settings,
                company_id=company_id,
                invoice_id=invoice.id,
                vendor_id=invoice.vendor_id,
                vendor_name=invoice.vendor_name,
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                amount=invoice.total or 0.0,
                payment_method=invoice.payment_method.value if invoice.payment_method else None,
            )
        except TransactionsServiceError as exc:
            # Logged loudly, not raised: the accountant's status flip above
            # already committed, and blocking it on a downstream hiccup
            # would be worse than a booking gap that needs a manual retry.
            logger.error("Failed to book expense for invoice %s: %s", invoice_id, exc)

    return InvoiceResponse.model_validate(invoice)
