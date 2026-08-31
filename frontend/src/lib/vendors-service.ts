/**
 * Vendors Service client — the supplier directory
 * (backend/services/vendors-service, port 8006, routed through the Gateway
 * at /api/v1/vendors).
 *
 * Same access-token-getter and `request()` shape as the other clients,
 * registered the same way in auth-context.tsx.
 */
const BASE = "/api/v1/vendors";

export type VendorStatus = "active" | "inactive" | "review";

export interface Vendor {
  id: string;
  name: string;
  category: string | null;
  city: string | null;
  ntn: string | null;
  payment_terms: string | null;
  /** 0-5, or null when nobody has rated this vendor yet. "Unrated" and
   *  "rated zero" are different statements about a supplier. */
  rating: number | null;
  status: VendorStatus;
  /** Derived from Invoice Service on every read, never stored — a cached
   *  aggregate would drift the moment an invoice is corrected. */
  total_spend_pkr: number;
  invoice_count: number;
  /** True when spend could not be fetched. The UI must show "unavailable"
   *  rather than a confident 0.00, which would be a lie rather than a gap. */
  spend_unavailable: boolean;
  created_at: string;
  updated_at: string;
}

export interface VendorListResponse {
  vendors: Vendor[];
  total: number;
  skip: number;
  limit: number;
}

export interface VendorOptions {
  categories: string[];
  payment_terms: string[];
}

export interface VendorCreatePayload {
  name: string;
  category?: string | null;
  city?: string | null;
  ntn?: string | null;
  payment_terms?: string | null;
  rating?: number | null;
  status?: VendorStatus;
}

export type VendorUpdatePayload = Partial<VendorCreatePayload>;

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

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
  // 204 No Content (delete) has no body to parse.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listVendors(
  params: {
    status?: VendorStatus;
    category?: string;
    city?: string;
    search?: string;
    skip?: number;
    limit?: number;
  } = {},
): Promise<VendorListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.category) query.set("category", params.category);
  if (params.city) query.set("city", params.city);
  if (params.search) query.set("search", params.search);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  return request<VendorListResponse>(`/?${query.toString()}`);
}

export function topVendors(limit = 5): Promise<Vendor[]> {
  return request<Vendor[]>(`/top?limit=${limit}`);
}

/** Suggested categories and payment terms. Suggestions only — the backend
 *  does not enforce them, because real suppliers do not fit a fixed list. */
export function vendorOptions(): Promise<VendorOptions> {
  return request<VendorOptions>("/options");
}

export function createVendor(payload: VendorCreatePayload): Promise<Vendor> {
  return request<Vendor>("/", { method: "POST", body: JSON.stringify(payload) });
}

export function updateVendor(vendorId: string, payload: VendorUpdatePayload): Promise<Vendor> {
  return request<Vendor>(`/${vendorId}`, { method: "PUT", body: JSON.stringify(payload) });
}

/** This vendor's invoices, proxied from Invoice Service. Returned in that
 *  service's own list shape — deliberately not remapped here, so there is
 *  one place to update when that contract changes. */
export function vendorInvoices(
  vendorId: string, params: { skip?: number; limit?: number } = {},
): Promise<{
  invoices: { id: string; invoice_number: string | null; invoice_date: string | null; total: number | null; status: string }[];
  total: number;
}> {
  const query = new URLSearchParams();
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request(`/${vendorId}/invoices?${query.toString()}`);
}

// ---------------------------------------------------------------------------
// Vendor reconciliation — turns a raw OCR vendor_name into a real vendor_id.
// Every scan writes a name, never an id; this is the accountant's queue for
// closing that gap. See docs/documents-and-vendors-plan.md §3.8.
// ---------------------------------------------------------------------------

export interface VendorMatchSuggestion {
  vendor_id: string;
  name: string;
  score: number;
}

export interface ReconciliationGroup {
  vendor_name: string;
  invoice_ids: string[];
  invoice_count: number;
  total_amount: number;
  sample_invoice_number: string | null;
  latest_invoice_date: string | null;
  suggestions: VendorMatchSuggestion[];
}

export interface ReconciliationQueue {
  groups: ReconciliationGroup[];
  ignored_count: number;
}

export interface IgnoredVendorName {
  id: string;
  vendor_name: string;
}

/** The whole queue, or a 502 if Invoice Service couldn't be reached — this
 *  response *is* downstream data, so a failure surfaces as an error rather
 *  than an empty (and misleading) queue. */
export function reconciliationQueue(): Promise<ReconciliationQueue> {
  return request<ReconciliationQueue>("/reconciliation/queue");
}

export function linkGroupToVendor(payload: {
  vendor_name: string; invoice_ids: string[]; vendor_id: string;
}): Promise<{ vendor_id: string; linked_count: number }> {
  return request("/reconciliation/link", { method: "POST", body: JSON.stringify(payload) });
}

export function createVendorAndLinkGroup(payload: {
  vendor_name: string; invoice_ids: string[];
  name: string; category?: string | null; city?: string | null;
  ntn?: string | null; payment_terms?: string | null;
}): Promise<{ vendor_id: string; linked_count: number }> {
  return request("/reconciliation/create-and-link", { method: "POST", body: JSON.stringify(payload) });
}

export function ignoreVendorName(vendorName: string): Promise<IgnoredVendorName> {
  return request<IgnoredVendorName>("/reconciliation/ignore", {
    method: "POST", body: JSON.stringify({ vendor_name: vendorName }),
  });
}

export function listIgnoredVendorNames(): Promise<IgnoredVendorName[]> {
  return request<IgnoredVendorName[]>("/reconciliation/ignored");
}

export async function unignoreVendorName(id: string): Promise<void> {
  await request<void>(`/reconciliation/ignored/${id}`, { method: "DELETE" });
}
