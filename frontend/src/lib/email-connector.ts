/**
 * Email Connector client.
 *
 * Mirrors slack-connector.ts's shape deliberately. Only exposes what the
 * backend actually implements — no stubbed functions that would 404 if
 * called. Notably absent vs Slack's client: conversations (a mailbox has
 * no channel/DM structure to group by) and retryDownload (there is no
 * retry endpoint yet).
 */
const BASE = "/api/v1/email";

export interface EmailConnectionStatus {
  connected: boolean;
  provider?: "gmail" | "outlook";
  email_address?: string | null;
  scopes?: string[];
  connected_at?: string;
  last_synced_at?: string | null;
  needs_reauth?: boolean;
}

/** Set by AuthProvider, same pattern as slack-connector.ts's — kept as its
 *  own module-level getter rather than shared, since each connector client
 *  is otherwise self-contained (matching the backend's per-service isolation). */
let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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

/**
 * Ask the connector for the Google consent URL.
 *
 * Fetched with the access token attached rather than navigating straight to
 * /connect: the route is company-scoped, and a browser navigation cannot
 * carry a bearer token. The caller navigates to the returned URL.
 */
export async function getEmailAuthorizeUrl(provider: "gmail" | "outlook" = "gmail"): Promise<string> {
  const { authorize_url } = await request<{ authorize_url: string }>(`/connect?provider=${provider}`);
  return authorize_url;
}

export function getEmailStatus(): Promise<EmailConnectionStatus> {
  return request<EmailConnectionStatus>("/status");
}

export function disconnectEmail(): Promise<{ connected: false }> {
  return request("/account", { method: "DELETE" });
}

export interface EmailAttachment {
  id: string;
  filename: string;
  mimetype: string | null;
  size: number;
  created_at: string;
  category: string;
  category_confidence: number;
  category_source: string;
  downloaded: boolean;
  download_failed: boolean;
  /** "imported" appears in Documents; "needs_review" is metadata-only and
   *  awaiting a decision; "rejected" was declined and is never downloaded. */
  review_status: "imported" | "needs_review" | "rejected";
  review_reason: string | null;
  message_id: string;
  /** Denormalised from the parent message so a document row can show its
   *  origin without a second request per attachment. */
  from_name: string | null;
  from_address: string | null;
  subject: string | null;
  received_at: string | null;
}

interface EmailAttachmentListResponse {
  attachments: EmailAttachment[];
  total: number;
  page: number;
  page_size: number;
}

export function listEmailFiles(
  params: {
    category?: string;
    /** Defaults server-side to "imported", so Documents never shows
     *  un-approved attachments. Pass "needs_review" for the tray. */
    reviewStatus?: "imported" | "needs_review" | "rejected" | "all";
    skip?: number;
    limit?: number;
  } = {},
): Promise<EmailAttachmentListResponse> {
  const query = new URLSearchParams();
  if (params.category) query.set("category", params.category);
  if (params.reviewStatus) query.set("review_status", params.reviewStatus);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<EmailAttachmentListResponse>(`/files/?${query.toString()}`);
}

export interface SenderRule {
  id: string;
  pattern: string;
  action: "allow" | "deny";
  created_at: string;
}

export function listSenderRules(): Promise<SenderRule[]> {
  return request<SenderRule[]>("/sender-rules/");
}

export function createSenderRule(pattern: string, action: "allow" | "deny"): Promise<SenderRule> {
  return request<SenderRule>("/sender-rules/", {
    method: "POST",
    body: JSON.stringify({ pattern, action }),
  });
}

export async function deleteSenderRule(ruleId: string): Promise<void> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/sender-rules/${ruleId}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  // 204 has no body, so the shared request() helper (which always parses
  // JSON) cannot be used here.
  if (!response.ok) throw new Error(`Could not delete this rule (${response.status})`);
}

/** `alsoRuleFor` turns a one-off decision into a standing rule — how the
 *  allow/deny lists actually get populated in practice. */
export function approveEmailAttachment(
  attachmentId: string, alsoRuleFor?: "address" | "domain",
): Promise<EmailAttachment> {
  return request<EmailAttachment>(`/files/${attachmentId}/approve`, {
    method: "POST",
    body: JSON.stringify(alsoRuleFor ? { also_rule_for: alsoRuleFor } : {}),
  });
}

export function rejectEmailAttachment(
  attachmentId: string, alsoRuleFor?: "address" | "domain",
): Promise<EmailAttachment> {
  return request<EmailAttachment>(`/files/${attachmentId}/reject`, {
    method: "POST",
    body: JSON.stringify(alsoRuleFor ? { also_rule_for: alsoRuleFor } : {}),
  });
}

export function retryEmailFile(attachmentId: string): Promise<EmailAttachment> {
  return request<EmailAttachment>(`/files/${attachmentId}/retry`, { method: "POST" });
}

/** `target="sale"` routes this same attachment to the Revenue Manager's
 *  scan-to-revenue endpoint instead of the purchase Scanner's — see
 *  slack-connector.ts's sendSlackFileToScanner for the identical reasoning
 *  (design spec docs/superpowers/specs/2026-08-27-revenue-manager-scan-
 *  design.md §8.4). Sent as form-urlencoded to match the backend's `Form`
 *  field, not JSON. */
export function sendEmailFileToScanner(
  attachmentId: string, target: "purchase" | "sale" = "purchase",
): Promise<{ job_id: string; status: string }> {
  return request(`/files/${attachmentId}/send-to-scanner`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ target }).toString(),
  });
}

/** Soft delete — hides this attachment from FinPilot only. The mailbox and
 *  the S3 copy are untouched, and a later sync will not bring it back. */
export async function deleteEmailFile(attachmentId: string): Promise<void> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/files/${attachmentId}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  // 204 has no body, so the shared request() helper (which always parses
  // JSON) cannot be used here.
  if (!response.ok) throw new Error(`Could not delete this file (${response.status})`);
}

export function updateEmailFileCategory(attachmentId: string, category: string): Promise<EmailAttachment> {
  return request<EmailAttachment>(`/files/${attachmentId}/category`, {
    method: "PATCH",
    body: JSON.stringify({ category }),
  });
}

export function startEmailSync(
  scope: { windowDays?: number | null } = {},
): Promise<{ sync_job_id: string; status: string; message: string }> {
  // Same model_fields_set contract as the Slack client's: only send the key
  // when the caller actually chose a window, so an unspecified sync leaves
  // the saved setting alone rather than resetting it.
  const body: { sync_window_days?: number | null } = {};
  if ("windowDays" in scope) body.sync_window_days = scope.windowDays;
  return request("/sync/", { method: "POST", body: JSON.stringify(body) });
}

export interface EmailSyncJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  messages_scanned: number;
  attachments_discovered: number;
  attachments_downloaded: number;
  attachments_failed: number;
  errors: string[];
}

export function getEmailSyncStatus(syncId: string): Promise<EmailSyncJob> {
  return request<EmailSyncJob>(`/sync/${syncId}`);
}

/**
 * Fetch a stored attachment's bytes as a Blob.
 *
 * Same reason as the Slack client's equivalent: `<iframe src>`/`<img src>`
 * cannot carry an Authorization header, so previewing has to go through
 * fetch() and an object URL.
 */
export async function fetchEmailFileBlob(attachmentId: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/files/${attachmentId}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not load this document (${response.status})`);
  }
  return response.blob();
}
