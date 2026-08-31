/**
 * Procurement Service client — purchase requests, purchase orders, and
 * vendor quote comparison (backend/services/procurement-service, port
 * 8005, routed through the Gateway at /api/v1/procurement).
 *
 * Same access-token-getter and `request()` shape as the other clients,
 * registered the same way in auth-context.tsx.
 */

export type PurchaseRequestStatus = "pending_approval" | "approved" | "rejected";
export type PurchaseOrderStatus = "in_transit" | "delivered" | "cancelled";

export interface PurchaseRequest {
  id: string;
  item_description: string;
  department: string;
  requester_name: string;
  amount_pkr: number;
  status: PurchaseRequestStatus;
  created_at: string;
  updated_at: string;
}

export interface PurchaseRequestListResponse {
  requests: PurchaseRequest[];
  total: number;
  skip: number;
  limit: number;
}

export interface PurchaseRequestOptions {
  departments: string[];
}

export interface PurchaseRequestCreatePayload {
  item_description: string;
  department: string;
  requester_name: string;
  amount_pkr: number;
}

export interface TimelineStep {
  title: string;
  detail: string;
  date: string | null;
  completed: boolean;
}

export interface PurchaseRequestTimeline {
  request_id: string;
  steps: TimelineStep[];
}

export interface PurchaseOrder {
  id: string;
  purchase_request_id: string | null;
  vendor_id: string | null;
  vendor_name: string;
  order_date: string;
  amount_pkr: number;
  expected_delivery: string;
  status: PurchaseOrderStatus;
  /** Computed — in_transit and past expected_delivery. Never a stored status. */
  delayed: boolean;
  created_at: string;
  updated_at: string;
}

export interface PurchaseOrderListResponse {
  orders: PurchaseOrder[];
  total: number;
  skip: number;
  limit: number;
}

export interface PurchaseOrderCreatePayload {
  purchase_request_id?: string | null;
  vendor_id?: string | null;
  vendor_name: string;
  order_date?: string | null;
  amount_pkr: number;
  expected_delivery: string;
}

export interface VendorQuote {
  id: string;
  purchase_request_id: string;
  vendor_id: string | null;
  vendor_name: string;
  price_pkr: number;
  delivery_estimate: string | null;
  quality_rating: string | null;
  payment_terms: string | null;
  score: number | null;
  created_at: string;
  updated_at: string;
}

export interface VendorQuoteListResponse {
  quotes: VendorQuote[];
  total: number;
}

export interface VendorQuoteCreatePayload {
  purchase_request_id: string;
  vendor_id?: string | null;
  vendor_name: string;
  price_pkr: number;
  delivery_estimate?: string | null;
  quality_rating?: string | null;
  payment_terms?: string | null;
  score?: number | null;
}

export interface ProcurementStats {
  pending_count: number;
  pending_amount_pkr: number;
  completed_count: number;
  delayed_count: number;
  cancelled_count: number;
}

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

const BASE = "/api/v1/procurement";

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

export function listRequests(
  params: {
    status?: PurchaseRequestStatus; department?: string; search?: string; skip?: number; limit?: number;
  } = {},
): Promise<PurchaseRequestListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.department) query.set("department", params.department);
  if (params.search) query.set("search", params.search);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  return request<PurchaseRequestListResponse>(`/requests/?${query.toString()}`);
}

export function requestOptions(): Promise<PurchaseRequestOptions> {
  return request<PurchaseRequestOptions>("/requests/options");
}

export function createRequest(payload: PurchaseRequestCreatePayload): Promise<PurchaseRequest> {
  return request<PurchaseRequest>("/requests/", { method: "POST", body: JSON.stringify(payload) });
}

export function approveRequest(requestId: string): Promise<PurchaseRequest> {
  return request<PurchaseRequest>(`/requests/${requestId}/approve`, { method: "POST" });
}

export function rejectRequest(requestId: string): Promise<PurchaseRequest> {
  return request<PurchaseRequest>(`/requests/${requestId}/reject`, { method: "POST" });
}

export function requestTimeline(requestId: string): Promise<PurchaseRequestTimeline> {
  return request<PurchaseRequestTimeline>(`/requests/${requestId}/timeline`);
}

export function listOrders(
  params: { status?: PurchaseOrderStatus; vendor_id?: string; skip?: number; limit?: number } = {},
): Promise<PurchaseOrderListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.vendor_id) query.set("vendor_id", params.vendor_id);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  return request<PurchaseOrderListResponse>(`/orders/?${query.toString()}`);
}

export function createOrder(payload: PurchaseOrderCreatePayload): Promise<PurchaseOrder> {
  return request<PurchaseOrder>("/orders/", { method: "POST", body: JSON.stringify(payload) });
}

export function updateOrderStatus(orderId: string, status: PurchaseOrderStatus): Promise<PurchaseOrder> {
  return request<PurchaseOrder>(`/orders/${orderId}/status`, { method: "PUT", body: JSON.stringify({ status }) });
}

export function listVendorQuotes(purchaseRequestId?: string): Promise<VendorQuoteListResponse> {
  const query = new URLSearchParams();
  if (purchaseRequestId) query.set("purchase_request_id", purchaseRequestId);
  return request<VendorQuoteListResponse>(`/vendor-comparison/?${query.toString()}`);
}

export function createVendorQuote(payload: VendorQuoteCreatePayload): Promise<VendorQuote> {
  return request<VendorQuote>("/vendor-comparison/", { method: "POST", body: JSON.stringify(payload) });
}

export function scoreVendorQuote(quoteId: string, score: number): Promise<VendorQuote> {
  return request<VendorQuote>(`/vendor-comparison/${quoteId}`, { method: "PUT", body: JSON.stringify({ score }) });
}

export function procurementStats(): Promise<ProcurementStats> {
  return request<ProcurementStats>("/stats");
}
