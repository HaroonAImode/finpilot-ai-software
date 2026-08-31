/**
 * Reports Service client — P&L, Cash Flow, Tax Summary, Sales, Purchase,
 * and (simplified) Balance Sheet reports (backend/services/reports-
 * service, port 8014, routed through the Gateway at /api/v1/reports).
 *
 * Same access-token-getter shape as the other clients, registered the
 * same way in auth-context.tsx — but PDF/Excel downloads need the actual
 * `Blob`, not JSON, so this file has its own `request()` variant for
 * those two, mirroring invoice-service.ts's own `fetchSalesInvoicePdf`.
 */

export type ReportType = "profit_loss" | "balance_sheet" | "cash_flow" | "tax_summary" | "sales" | "purchases";

export interface SummaryLine {
  label: string;
  value: string;
}

export interface ReportTable {
  headers: string[];
  rows: string[][];
}

export interface ReportPayload {
  title: string;
  company_name: string | null;
  period_label: string;
  summary: SummaryLine[];
  table: ReportTable | null;
  notes: string[];
}

export interface Report {
  id: string;
  type: ReportType;
  period_start: string;
  period_end: string;
  status: string;
  payload: ReportPayload;
  generated_at: string;
  created_at: string;
}

export interface ReportListItem {
  id: string;
  type: ReportType;
  period_start: string;
  period_end: string;
  status: string;
  generated_at: string;
}

export interface ReportListResponse {
  reports: ReportListItem[];
  total: number;
  skip: number;
  limit: number;
}

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

const BASE = "/api/v1/reports";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
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

async function requestBlob(path: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error(`Request to ${path} failed with ${response.status}`);
  return response.blob();
}

export function createProfitLoss(periodStart: string, periodEnd: string): Promise<Report> {
  return request<Report>("/profit-loss", {
    method: "POST", body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
  });
}

export function createCashFlow(periodStart: string, periodEnd: string): Promise<Report> {
  return request<Report>("/cash-flow", {
    method: "POST", body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
  });
}

export function createTaxSummary(periodStart: string, periodEnd: string): Promise<Report> {
  return request<Report>("/tax-summary", {
    method: "POST", body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
  });
}

export function createSalesReport(periodStart: string, periodEnd: string): Promise<Report> {
  return request<Report>("/sales", {
    method: "POST", body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
  });
}

export function createPurchaseReport(periodStart: string, periodEnd: string): Promise<Report> {
  return request<Report>("/purchases", {
    method: "POST", body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
  });
}

export function createBalanceSheet(asOfDate: string): Promise<Report> {
  return request<Report>("/balance-sheet", { method: "POST", body: JSON.stringify({ as_of_date: asOfDate }) });
}

export function listReports(params: { type?: ReportType; skip?: number; limit?: number } = {}): Promise<ReportListResponse> {
  const query = new URLSearchParams();
  if (params.type) query.set("type", params.type);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<ReportListResponse>(`/?${query.toString()}`);
}

export function getReport(reportId: string): Promise<Report> {
  return request<Report>(`/${reportId}`);
}

export function fetchReportPdf(reportId: string): Promise<Blob> {
  return requestBlob(`/${reportId}/pdf`);
}

export function fetchReportExcel(reportId: string): Promise<Blob> {
  return requestBlob(`/${reportId}/excel`);
}
