/**
 * Documents Service client — the browser-upload document library
 * (backend/services/documents-service, port 8013, routed through the Gateway
 * at /api/v1/documents).
 *
 * Mirrors the connector clients' shape (same access-token-getter pattern,
 * same `request()` helper) so the Documents page can treat this as one more
 * source. The difference from a connector: there is no OAuth and no sync —
 * documents arrive because a user uploaded them — and it is the only source
 * that supports deleting.
 */
const BASE = "/api/v1/documents";

/** The backend's own category vocabulary (lowercase enum values). The
 *  Documents page uses Title Case labels, so the two are mapped rather than
 *  assumed identical — see documents.ts's browserDocumentToDocument. */
export type DocumentServiceCategory =
  | "invoices" | "receipts" | "reports" | "contracts" | "images" | "other";

export type ScannerStatus = "not_sent" | "sent" | "failed";

export interface StoredDocument {
  id: string;
  filename: string;
  mimetype: string | null;
  size: number;
  category: DocumentServiceCategory;
  scanner_status: ScannerStatus;
  invoice_id: string | null;
  created_at: string;
}

export interface StoredDocumentListResponse {
  documents: StoredDocument[];
  total: number;
  skip: number;
  limit: number;
}

export interface SendToScannerResult {
  document_id: string;
  invoice_id: string | null;
  scanner_status: ScannerStatus;
  message: string;
}

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
  // 204 No Content (delete) has no body to parse.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function uploadDocument(file: File, category?: DocumentServiceCategory): Promise<StoredDocument> {
  const formData = new FormData();
  formData.append("file", file);
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  return request<StoredDocument>(`/upload${query}`, { method: "POST", body: formData });
}

export function listDocuments(
  params: { skip?: number; limit?: number; category?: DocumentServiceCategory } = {},
): Promise<StoredDocumentListResponse> {
  const query = new URLSearchParams();
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  if (params.category) query.set("category", params.category);
  return request<StoredDocumentListResponse>(`/?${query.toString()}`);
}

export function updateDocumentCategory(
  documentId: string, category: DocumentServiceCategory,
): Promise<StoredDocument> {
  return request<StoredDocument>(`/${documentId}/category`, {
    method: "PATCH", body: JSON.stringify({ category }),
  });
}

/** Soft delete server-side: the row and stored file survive, but the document
 *  stops being listed or fetchable. See the service's own delete endpoint for
 *  why an already-scanned invoice must not lose its source file. */
export function deleteDocument(documentId: string): Promise<void> {
  return request<void>(`/${documentId}`, { method: "DELETE" });
}

export function sendDocumentToScanner(documentId: string): Promise<SendToScannerResult> {
  return request<SendToScannerResult>(`/${documentId}/send-to-scanner`, { method: "POST" });
}

/** Fetches the stored bytes for inline preview. Same reason as every other
 *  source's fetchBlob: an <iframe src>/<img src> cannot carry an
 *  Authorization header, so previewing has to go through fetch(). */
export async function fetchDocumentBlob(documentId: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/${documentId}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not load this document (${response.status})`);
  }
  return response.blob();
}
