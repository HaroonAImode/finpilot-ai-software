const BASE = "/api/v1/slack";

export interface SlackConnectionStatus {
  connected: boolean;
  workspace_name?: string | null;
  scopes?: string[];
  installed_at?: string;
}

export interface SlackFile {
  id: string;
  slack_file_id: string;
  filename: string;
  title: string | null;
  file_type: string;
  mimetype: string | null;
  size: number;
  created_at: string;
  is_external: boolean;
  category: string;
  category_confidence: number;
  category_source: string;
  downloaded: boolean;
  download_failed: boolean;
  shared_by_user_name: string | null;
  slack_permalink: string;
  conversation_id: string | null;
}

export interface SlackConversation {
  id: string;
  slack_conversation_id: string;
  name: string;
  conversation_type: "public" | "private" | "im" | "mpim";
  file_count: number;
  last_synced: string | null;
  last_error: string | null;
  last_error_hint: string | null;
}

interface ConversationListResponse {
  conversations: SlackConversation[];
  total: number;
  needs_attention: number;
}

export interface SlackSyncJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  total_conversations: number;
  conversations_processed: number;
  files_discovered: number;
  files_downloaded: number;
  files_failed: number;
  errors: string[];
}

interface FileListResponse {
  files: SlackFile[];
  total: number;
  page: number;
  page_size: number;
}

/** Set by AuthProvider so this client can attach the current access token
 *  without importing React state (which would make it unusable outside a
 *  component) or holding a stale copy of a token that rotates. */
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
  // 204 No Content (delete) has no body to parse.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/**
 * Ask the connector for the Slack consent URL.
 *
 * Fetched with the access token attached rather than navigating straight to
 * `/connect`: the route is company-scoped, and a browser navigation cannot carry
 * a bearer token. The caller navigates to the returned URL.
 */
export async function getSlackAuthorizeUrl(): Promise<string> {
  const { authorize_url } = await request<{ authorize_url: string }>("/connect");
  return authorize_url;
}

export function getSlackStatus(): Promise<SlackConnectionStatus> {
  return request<SlackConnectionStatus>("/status");
}

export function disconnectSlack(): Promise<{ connected: false }> {
  return request("/installation", { method: "DELETE" });
}

export function startSlackSync(
  scope: { conversationIds?: string[]; windowDays?: number | null } = {},
): Promise<{ sync_job_id: string; status: string; message: string }> {
  // window_days must only be sent when the caller actually chose one — Pydantic
  // tells "explicitly All Time" (null) apart from "leave the saved setting
  // alone" (omitted) by whether the key is present at all, so an unconditional
  // `windowDays ?? null` here would silently reset every plain "Sync now" click
  // back to All Time.
  const body: { conversation_ids?: string[]; window_days?: number | null } = {};
  if (scope.conversationIds && scope.conversationIds.length > 0) {
    body.conversation_ids = scope.conversationIds;
  }
  if ("windowDays" in scope) {
    body.window_days = scope.windowDays;
  }
  return request("/sync/", { method: "POST", body: JSON.stringify(body) });
}

export function getSyncStatus(syncId: string): Promise<SlackSyncJob> {
  return request<SlackSyncJob>(`/sync/${syncId}`);
}

export function listSlackFiles(
  params: { category?: string; conversationId?: string; skip?: number; limit?: number } = {},
): Promise<FileListResponse> {
  const query = new URLSearchParams();
  if (params.category) query.set("category", params.category);
  if (params.conversationId) query.set("conversation_id", params.conversationId);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<FileListResponse>(`/files/?${query.toString()}`);
}

/**
 * Conversations FinPilot knows about, with per-conversation sync status.
 *
 * Cheap — reads only what previous syncs already stored, no Slack calls — so
 * it is what the grouped Documents view fetches first; each group's files are
 * then loaded lazily via listSlackFiles({ conversationId }) only when expanded.
 */
export function listSlackConversations(): Promise<ConversationListResponse> {
  return request<ConversationListResponse>("/conversations/");
}

/**
 * Live Slack discovery — metadata only, no message walk, no downloads.
 *
 * listSlackConversations() only shows what a previous sync already stored,
 * which is empty right after connecting. The picker calls this first so it
 * always has a complete, correctly-named list to choose from, even before
 * any sync has ever run.
 */
export function refreshSlackConversations(): Promise<ConversationListResponse> {
  return request<ConversationListResponse>("/conversations/refresh", { method: "POST" });
}

export function updateSlackFileCategory(fileId: string, category: string): Promise<SlackFile> {
  return request<SlackFile>(`/files/${fileId}/category`, {
    method: "PATCH",
    body: JSON.stringify({ category }),
  });
}

export function retrySlackFile(fileId: string): Promise<SlackFile> {
  return request<SlackFile>(`/files/${fileId}/retry`, { method: "POST" });
}

/** `target="sale"` routes this same file to the Revenue Manager's
 *  scan-to-revenue endpoint instead of the purchase Scanner's (design spec
 *  docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md §8.4).
 *  Sent as `application/x-www-form-urlencoded`, not JSON — the backend
 *  route declares `target` as a Form field, matching every other
 *  connector-to-Invoice-Service hand-off's multipart/form shape. */
export function sendSlackFileToScanner(
  fileId: string, target: "purchase" | "sale" = "purchase",
): Promise<{ job_id: string; status: string }> {
  return request(`/files/${fileId}/send-to-scanner`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ target }).toString(),
  });
}

/** Soft delete — hides this file from FinPilot only. Slack and the original
 *  S3 copy are untouched, and a later sync will not bring it back. */
export async function deleteSlackFile(fileId: string): Promise<void> {
  await request<void>(`/files/${fileId}`, { method: "DELETE" });
}

export function getSlackFilePreview(fileId: string): Promise<{ url: string; filename: string; mimetype: string }> {
  return request(`/files/${fileId}/preview`);
}

/**
 * Fetch a stored file's bytes as a Blob.
 *
 * Needed because `<iframe src>` and `<img src>` are browser-initiated requests
 * that cannot carry an Authorization header — pointing them straight at
 * /content returns 401 now that the route is authenticated. Fetching here with
 * the token attached and handing the caller an object URL keeps the endpoint
 * protected while still rendering inline.
 */
export async function fetchSlackFileBlob(fileId: string): Promise<Blob> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}/files/${fileId}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Could not load this document (${response.status})`);
  }
  return response.blob();
}
