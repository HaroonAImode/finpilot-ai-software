/**
 * Invoice Service client — the AI Invoice Scanner's backend
 * (backend/services/invoice-service, routed through the Gateway at
 * /api/v1/invoices). Mirrors slack-connector.ts/email-connector.ts's shape:
 * same access-token-getter pattern, same request() helper, registered the
 * same way in auth-context.tsx.
 */
const BASE = "/api/v1/invoices";

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request to ${path} failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export type InvoiceStatus =
  | "processed" | "needs_review" | "needs_review_high_priority" | "validated" | "sent_to_accounting"
  /** A human decided this document should not be processed. A soft state —
   *  the file, extraction and audit trail all stay, and it is reversible. */
  | "rejected";

/** Whether a document belongs in financial transaction processing at all.
 *  A separate axis from InvoiceStatus, which tracks the human workflow:
 *  a confirmed internal memo is non_transactional + validated. */
export type TransactionStatus = "transactional" | "non_transactional";

/** Who decided `transaction_status`. The rules engine's original verdict is
 *  never overwritten — it stays in the stored extraction — so an override
 *  stays auditable. */
export type ClassificationSource = "rule" | "user_override";

export type PaymentMethod = "bank" | "cash";

/** Display terms for `PaymentMethod` — used everywhere a value is shown to
 *  a user (Saved Records, Scanner), so the same stored value never reads
 *  as two different words in two different places. "bank" reads as
 *  "Online" here rather than being renamed in the database: the two mean
 *  the same non-cash-payment fact, and renaming the stored enum value
 *  would need a migration for a purely cosmetic change. */
export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
  bank: "Online",
  cash: "Cash",
};

/** The label for a possibly-unset payment method — "Not Specified" is the
 *  Cash Book's own term for "a human hasn't said yet", distinct from any
 *  real payment method and never silently folded into Cash or Online. */
export function paymentMethodLabel(method: PaymentMethod | null | undefined): string {
  return method ? PAYMENT_METHOD_LABELS[method] : "Not Specified";
}

export interface FieldValue {
  value: string | number | null;
  confidence: number;
  method: string;
  page?: number | null;
  bbox?: [number, number, number, number] | null;
}

export interface InvoiceItem {
  id: string;
  position: number;
  description: string | null;
  qty: number | null;
  rate: number | null;
  amount: number | null;
  arithmetic_check: "pass" | "fail" | "not_checked";
  review_flags: string[];
}

export interface Invoice {
  id: string;
  type: "purchase" | "sale";
  status: InvoiceStatus;
  transaction_status: TransactionStatus;
  classification_source: ClassificationSource;
  /** An amount a non-transactional document *states*. Never a total. */
  amount_mentioned: number | null;
  /** What the rules engine decided this document is ("minute_sheet",
   *  "invoice", ...). Keeps reporting the engine's own verdict even after a
   *  human overrides `transaction_status`. */
  detected_document_type: string | null;
  /** The document's own wording behind that decision, quoted verbatim —
   *  shown as the reason, so the classification is explainable. */
  classification_reason: string | null;
  vendor_name: string | null;
  /** Set on a scanned sale (Revenue Manager) or a generated sales invoice —
   *  null on a purchase, where the counterparty is a vendor, not a
   *  customer. See invoice_builder.py's own docstring for how this is
   *  derived on a scan: sourced from the dedicated field, falling back to
   *  vendor_name, since the rules engine reports a sale's counterparty
   *  under that key regardless of which party it actually is. */
  customer_name: string | null;
  /** Saved Records' cashbook category (Office Entertainment, Employee
   *  Care, etc.) — null until a human sets one, shown as "Uncategorized". */
  category: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  ntn: string | null;
  subtotal: number | null;
  tax_rate: number | null;
  tax_amount: number | null;
  total: number | null;
  /** How this invoice was paid — never extracted by the rules engine, set
   * manually by a human after reviewing the scan. null until they choose. */
  payment_method: PaymentMethod | null;
  document_confidence: number;
  /** Per-field confidence (0.0-1.0), keyed by field name. A field missing
   * from this map means the rules engine never found it at all — distinct
   * from a low-but-present confidence, which the review UI shows differently. */
  field_confidence: Record<string, number>;
  /** Per-field {page, bbox} for the bounding-box review UI (Scanner v2) —
   * where on the document each value came from. Same "missing means not
   * found" rule as field_confidence. */
  field_locations: Record<string, { page: number; bbox: [number, number, number, number] }>;
  /** (width, height) per page, native units matching field_locations' bbox
   * space — [] for an invoice scanned before this field existed. */
  page_dimensions: [number, number][];
  /** Read-only detected fields beyond the eight correctable header fields —
   * customer_name, currency, document_type, payment_status, city, country.
   * Same "absent means no evidence found" rule as field_confidence: a
   * currency the engine never saw is simply missing here, never defaulted
   * to a guess. Backed by Invoice.extracted_fields server-side. */
  extracted_fields: Record<string, { value: string | number; confidence: number; status: "FOUND" | "NOT_FOUND" | "UNCERTAIN" }>;
  /** Vendor-specific labelled fields the rules engine found but has no
   * canonical name for ("Shipping & Handling", "PO #", "Payment Method").
   * `label` is the document's own printed wording; `bbox`/`page` locate the
   * value on the page, so these highlight exactly like a canonical field. */
  dynamic_fields: {
    key: string;
    label: string;
    value: string;
    confidence: number;
    page: number | null;
    bbox: [number, number, number, number] | null;
  }[];
  extraction_source: "pdf_text" | "ocr";
  review_flags: string[];
  filename: string;
  mimetype: string | null;
  size: number;
  created_at: string;
  updated_at: string;
  items: InvoiceItem[];
}

export interface InvoiceListItem {
  id: string;
  type: "purchase" | "sale";
  status: InvoiceStatus;
  transaction_status: TransactionStatus;
  classification_source: ClassificationSource;
  /** An amount a non-transactional document *states* ("For approval of Rs.
   *  22,875/-"). Never a transaction total — shown as "Amount mentioned". */
  amount_mentioned: number | null;
  /** What the rules engine decided this document is ("minute_sheet",
   *  "invoice", ...). Keeps reporting the engine's own verdict even after a
   *  human overrides `transaction_status`. */
  detected_document_type: string | null;
  /** The document's own wording behind that decision, quoted verbatim —
   *  shown as the reason, so the classification is explainable. */
  classification_reason: string | null;
  vendor_name: string | null;
  customer_name: string | null;
  category: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  ntn: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  total: number | null;
  payment_method: PaymentMethod | null;
  document_confidence: number;
  /** null for a generated sales invoice — it has no source document. */
  filename: string | null;
  mimetype: string | null;
  created_at: string;
}

export interface InvoiceListResponse {
  invoices: InvoiceListItem[];
  total: number;
  skip: number;
  limit: number;
}

export interface ScanJobResponse {
  job_id: string;
  status: "queued" | "processing" | "done" | "failed";
  invoice_id: string | null;
  invoice: Invoice | null;
  error: string | null;
  /** Document Preprocessing (docs/invoice-ocr-plan.md §7) — populated only
   *  when the uploaded photo confidently contained more than one separate
   *  document, each of which is its own real invoice. Empty for the
   *  overwhelming common case. */
  additional_invoice_ids: string[];
}

/** Uploads a file straight from the browser (the drag-and-drop / file-picker
 * path). Sending from a connected app instead goes through that connector's
 * own /files/{id}/send-to-scanner bridge, which calls this same backend
 * endpoint server-to-server — this function is only for a local file. */
export function scanInvoice(file: File): Promise<ScanJobResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("type", "purchase");
  return request<ScanJobResponse>("/scan", { method: "POST", body: formData });
}

export function getScanStatus(jobId: string): Promise<ScanJobResponse> {
  return request<ScanJobResponse>(`/scan/${jobId}`);
}

/** The camera-capture flow's own fast preview crop — proxies to AI
 * Engine's smart-crop (no OCR, nothing persisted) so a freshly-captured
 * photo can show an auto-cropped preview before the shot is ever scanned.
 * Same auth-via-fetch-then-blob reasoning as fetchInvoiceContent: this is
 * a raw image response, not JSON, so it bypasses request()'s own
 * Content-Type: application/json assumption. */
export async function autoCropPreview(file: File): Promise<Blob> {
  const token = accessTokenGetter();
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${BASE}/auto-crop-preview`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not auto-crop this photo (${response.status})`);
  }
  return response.blob();
}

export function listInvoices(
  params: {
    status?: InvoiceStatus | InvoiceStatus[] | "all"; skip?: number; limit?: number;
    /** "YYYY-MM-DD" inclusive bounds — Saved Records' month filter. */
    dateFrom?: string; dateTo?: string;
    /** Omit for both — the Non-Transactional Documents area passes
     *  "non_transactional"; the cashbook passes "transactional". */
    transactionStatus?: TransactionStatus;
  } = {},
): Promise<InvoiceListResponse> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") {
    query.set("status", Array.isArray(params.status) ? params.status.join(",") : params.status);
  }
  if (params.transactionStatus) query.set("transaction_status", params.transactionStatus);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<InvoiceListResponse>(`/?${query.toString()}`);
}

export function getInvoice(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}`);
}

export interface InvoiceStatusSummary {
  counts: Record<InvoiceStatus, number>;
  total: number;
}

/** Purchase document counts by status — the Dashboard's Invoice Status
 *  chart. Real status names only (there is no "Duplicate" status in this
 *  schema — dedup rejects a re-upload outright rather than tagging one). */
export function getInvoiceStatusSummary(): Promise<InvoiceStatusSummary> {
  return request<InvoiceStatusSummary>("/status-summary");
}

export interface InvoiceItemUpdate {
  description?: string | null;
  qty?: number | null;
  rate?: number | null;
  amount?: number | null;
}

export interface InvoiceUpdatePayload {
  vendor_name?: string | null;
  /** Set on generated sales invoices; correctable from the Records grid. */
  customer_name?: string | null;
  /** One of the suggested categories from invoiceCategoryOptions(), or any
   *  other free text — not validated against the list server-side. An
   *  explicit null clears it back to "Uncategorized". */
  category?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  ntn?: string | null;
  subtotal?: number | null;
  tax_rate?: number | null;
  tax_amount?: number | null;
  total?: number | null;
  payment_method?: PaymentMethod | null;
  /** Only the two *saved* states are accepted — the backend refuses anything
   *  else, and refuses even these on an invoice that has not been validated.
   *  The grid is not a way around the extraction lifecycle. */
  status?: Extract<InvoiceStatus, "validated" | "sent_to_accounting"> | null;
  items?: InvoiceItemUpdate[];
}

export function updateInvoice(invoiceId: string, payload: InvoiceUpdatePayload): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function validateInvoice(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}/validate`, { method: "POST" });
}

/** "Reject" — a soft, reversible state. Deliberately not a delete: the
 *  source document, extraction and audit trail are all preserved. */
export function rejectInvoice(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}/reject`, { method: "POST" });
}

/** "Process as Transaction" (and its reverse) — a human disagreeing with the
 *  system's classification. Records `classification_source: "user_override"`
 *  rather than silently overwriting what the rules engine decided. */
export function reclassifyInvoice(
  invoiceId: string, transactionStatus: TransactionStatus,
): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}/reclassify`, {
    method: "POST",
    body: JSON.stringify({ transaction_status: transactionStatus }),
  });
}

export function sendToAccounting(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/${invoiceId}/send-to-accounting`, { method: "POST" });
}

/** Fetches the original uploaded file as a Blob — same reason as every
 * other source's fetchBlob: an <iframe src>/<img src> cannot carry an
 * Authorization header, so previewing has to go through fetch(). */
export async function fetchInvoiceContent(invoiceId: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/${invoiceId}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not load this document (${response.status})`);
  }
  return response.blob();
}

/** Fetches one page of the document rasterized to PNG — the bounding-box
 * review UI's document image (Scanner v2). Same auth-via-fetch reasoning as
 * fetchInvoiceContent above. */
export async function fetchInvoicePage(invoiceId: string, pageNumber: number): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/${invoiceId}/page/${pageNumber}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not load this page (${response.status})`);
  }
  return response.blob();
}

// ---------------------------------------------------------------------------
// Saved Records — categorized cashbook view (docs/superpowers/specs/
// 2026-09-01-saved-records-cashbook-design.md). Category options and the
// accurate per-category summary; PDF report download.
// ---------------------------------------------------------------------------

export interface InvoiceOptions {
  categories: string[];
}

/** Suggested cashbook categories — same suggestion-list shape as every
 *  other *Options() call in this codebase (HR Service, Settings, Vendors). */
export function invoiceCategoryOptions(): Promise<InvoiceOptions> {
  return request<InvoiceOptions>("/options");
}

export interface CategorySummary {
  /** null represents every invoice with no category set — shown as
   *  "Uncategorized" everywhere this is displayed. */
  category: string | null;
  count: number;
  total: number;
}

/** "YYYY-MM-DD" inclusive bounds — the same month-filter shape every
 *  category/report call below accepts. */
export interface MonthRange {
  dateFrom?: string;
  dateTo?: string;
}

function monthRangeParams(range?: MonthRange): Record<string, string> {
  const params: Record<string, string> = {};
  if (range?.dateFrom) params["date_from"] = range.dateFrom;
  if (range?.dateTo) params["date_to"] = range.dateTo;
  return params;
}

/** The one call every category/grand total on Saved Records reads from —
 *  a backend SQL aggregate over the *entire* matching row set, not a
 *  client-side sum of whatever page happens to be loaded. See the design
 *  spec §7 for why this is the only place a total is allowed to come from. */
export function getCategorySummary(range?: MonthRange): Promise<CategorySummary[]> {
  const query = new URLSearchParams(monthRangeParams(range)).toString();
  return request<CategorySummary[]>(`/categories/summary${query ? `?${query}` : ""}`);
}

/** Every "YYYY-MM" that actually has a saved record — backs the History
 *  month picker so it never offers a month with nothing in it. */
export function getAvailableMonths(): Promise<string[]> {
  return request<string[]>("/categories/available-months");
}

/** The Cash Book's Total/Cash/Online/Not-Specified/Transactions strip for
 *  a period — `cash_total + online_total + unspecified_total` always
 *  equals `monthly_total` by construction. Same backend-aggregate-only
 *  discipline as getCategorySummary: never re-derived from whatever page
 *  of records happens to be loaded client-side. */
export interface MonthlyPaymentSummary {
  monthly_total: number;
  cash_total: number;
  online_total: number;
  unspecified_total: number;
  transaction_count: number;
}

export function getMonthlySummary(range?: MonthRange): Promise<MonthlyPaymentSummary> {
  const query = new URLSearchParams(monthRangeParams(range)).toString();
  return request<MonthlyPaymentSummary>(`/monthly-summary${query ? `?${query}` : ""}`);
}

/** Downloads a category's branded PDF report — entries table with an
 *  accurate totals row (the same aggregate getCategorySummary reads),
 *  followed by every entry's actual receipt/invoice image. Omit `category`
 *  for a single report covering everything; pass "Uncategorized" for the
 *  no-category bucket. `range` scopes the report to the same month Saved
 *  Records' History filter is showing, so the downloaded numbers always
 *  match what was on screen. Same auth-via-fetch-then-blob reasoning as
 *  fetchInvoiceContent — this is a browser download, not a same-origin link. */
export async function fetchCategoryReport(category?: string | null, range?: MonthRange): Promise<Blob> {
  const token = accessTokenGetter();
  const params = { ...(category ? { category } : {}), ...monthRangeParams(range) };
  const query = Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : "";
  const response = await fetch(`${BASE}/categories/report.pdf${query}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not generate this report (${response.status})`);
  }
  return response.blob();
}

// ---------------------------------------------------------------------------
// Invoice Generator — architecture report §5.3's sales half. A separate
// section because nothing here carries a confidence score: these numbers are
// authored by the user, not extracted, so this is a plain CRUD + PDF client.
// ---------------------------------------------------------------------------

export interface SalesLineItem {
  id: string;
  position: number;
  description: string | null;
  qty: number | null;
  rate: number | null;
  amount: number | null;
}

export interface SalesLineItemInput {
  description: string;
  qty: number;
  rate: number;
}

export interface SalesInvoice {
  id: string;
  type: "sale";
  status: string;
  customer_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  ntn: string | null;
  subtotal: number | null;
  tax_rate: number | null;
  tax_amount: number | null;
  total: number | null;
  items: SalesLineItem[];
}

export interface SalesInvoiceListResponse {
  invoices: SalesInvoice[];
  total: number;
  skip: number;
  limit: number;
}

export interface SalesInvoiceCreatePayload {
  customer_name: string;
  invoice_number?: string | null;
  invoice_date?: string | null;
  ntn?: string | null;
  /** Fractional, not a percentage — 0.17 means 17%, matching the backend. */
  tax_rate?: number;
  items: SalesLineItemInput[];
}

export function createSalesInvoice(payload: SalesInvoiceCreatePayload): Promise<SalesInvoice> {
  return request<SalesInvoice>("/sales/", { method: "POST", body: JSON.stringify(payload) });
}

export function listSalesInvoices(
  params: { skip?: number; limit?: number } = {},
): Promise<SalesInvoiceListResponse> {
  const query = new URLSearchParams();
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  return request<SalesInvoiceListResponse>(`/sales/?${query.toString()}`);
}

export function getSalesInvoice(invoiceId: string): Promise<SalesInvoice> {
  return request<SalesInvoice>(`/sales/${invoiceId}`);
}

/** Same auth-via-fetch reasoning as fetchInvoiceContent: a plain <a href>
 *  to /pdf carries no token and would 401. */
export async function fetchSalesInvoicePdf(invoiceId: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/sales/${invoiceId}/pdf`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not generate this PDF (${response.status})`);
  }
  return response.blob();
}

// ---------------------------------------------------------------------------
// Revenue Manager — scan-to-revenue. Design spec:
// docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md.
//
// A third way to create a `type=sale` Invoice row, alongside the Invoice
// Generator above (typed) and nothing else — this one is scanned, exactly
// like the purchase Scanner. Reuses that Scanner's own Invoice/
// InvoiceListResponse/ScanJobResponse shapes unchanged: the backend
// endpoints return the identical rules-engine extraction result, just
// aimed at `type=sale`, so there is nothing here for a second set of
// types to describe.
// ---------------------------------------------------------------------------

export function scanSalesInvoice(file: File): Promise<ScanJobResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return request<ScanJobResponse>("/sales/scan", { method: "POST", body: formData });
}

export function getSalesScanStatus(jobId: string): Promise<ScanJobResponse> {
  return request<ScanJobResponse>(`/sales/scan/${jobId}`);
}

export function listScannedSales(
  params: { status?: InvoiceStatus | InvoiceStatus[] | "all"; skip?: number; limit?: number } = {},
): Promise<InvoiceListResponse> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") {
    query.set("status", Array.isArray(params.status) ? params.status.join(",") : params.status);
  }
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<InvoiceListResponse>(`/sales/scanned?${query.toString()}`);
}

export interface MonthlyRevenuePoint {
  month: string;
  total: number;
  count: number;
}

export interface TopCustomerPoint {
  customer_name: string;
  total: number;
  count: number;
}

export interface SalesSummaryResponse {
  monthly: MonthlyRevenuePoint[];
  top_customers: TopCustomerPoint[];
  totals: {
    revenue: number;
    document_count: number;
    pending_review: number;
    /** Sales dated today (or scanned today, as a fallback) — added for
     *  Transactions Service's "Today's Revenue" Dashboard KPI. */
    today_revenue: number;
  };
}

export function getSalesSummary(): Promise<SalesSummaryResponse> {
  return request<SalesSummaryResponse>("/sales/summary");
}
