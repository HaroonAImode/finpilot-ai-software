/**
 * Source-agnostic document layer.
 *
 * Every connector (Slack today; Email and WhatsApp planned) is its own backend
 * service per the architecture report, so the frontend is the only place that
 * sees them together. This module defines one shape all sources map into, plus
 * a registry the Documents page renders from — adding the Email connector means
 * writing an adapter here and flipping its status, not touching the page.
 *
 * Sources that do not exist yet are declared `planned` rather than omitted, so
 * the UI can show an honest "not connected" column instead of pretending the
 * source is empty.
 */
import { Mail, MessageCircle, Slack, UploadCloud, type LucideIcon } from "lucide-react";
import {
  deleteSlackFile, fetchSlackFileBlob, listSlackConversations, listSlackFiles,
  refreshSlackConversations, retrySlackFile, sendSlackFileToScanner, startSlackSync,
  updateSlackFileCategory, getSlackStatus,
  type SlackConversation, type SlackFile,
} from "@/lib/slack-connector";
import {
  deleteEmailFile, fetchEmailFileBlob, getEmailStatus, listEmailFiles, retryEmailFile,
  sendEmailFileToScanner, startEmailSync, updateEmailFileCategory, type EmailAttachment,
} from "@/lib/email-connector";
import {
  deleteDocument, fetchDocumentBlob, listDocuments, sendDocumentToScanner,
  updateDocumentCategory, uploadDocument,
  type DocumentServiceCategory, type StoredDocument,
} from "@/lib/documents-service";

export type DocumentSourceId = "slack" | "email" | "whatsapp" | "browser";

/** "flat" is every document in one list. "grouped" organises them by the
 *  conversation they came from — a Slack channel or DM today, an email
 *  thread or WhatsApp chat once those connectors exist. */
export type DocumentView = "flat" | "grouped";

/** How the flat view renders each file — a plain row, or an icon tile at
 *  one of two sizes, the way a real file manager's view menu works. */
export type FileBrowserViewMode = "list" | "small-icons" | "large-icons";

/** Categories the AI Scanner can extract structured invoice data from. */
export const SCANNER_ELIGIBLE = new Set(["Invoices", "Receipts", "Spreadsheets & Financial"]);

/** "Other" is a person explicitly saying "this isn't a financial document" —
 *  distinct from "uncategorized", which just means the rules engine couldn't
 *  guess. Conflating the two would hide a real user decision inside an
 *  auto-generated bucket, so both appear here as separate, pickable values. */
export const DOCUMENT_CATEGORIES = [
  "Invoices", "Receipts", "Contracts", "Project Plans", "Reports", "Presentations",
  "Spreadsheets & Financial", "Images & Screenshots", "Bookings & Reservations",
  "Archives", "Audio & Video", "Other", "uncategorized",
];

export interface UnifiedDocument {
  id: string;
  source: DocumentSourceId;
  filename: string;
  title: string | null;
  fileType: string;
  mimetype: string | null;
  size: number;
  createdAt: string;
  category: string;
  categorySource: string;
  downloaded: boolean;
  downloadFailed: boolean;
  sharedBy: string | null;
  /** Link back to the document where it was originally shared. */
  externalUrl: string | null;
  /** Same-origin URL that streams the bytes for inline preview, or null if not stored yet. */
  contentUrl: string | null;
  scannerEligible: boolean;
  /** Which conversation this came from, for sources that group by one. Null
   *  for a source that has no such concept. */
  conversationId: string | null;
}

export interface DocumentPage {
  documents: UnifiedDocument[];
  total: number;
}

/** A channel, DM, or equivalent grouping a source's documents by origin. */
export interface UnifiedConversation {
  id: string;
  name: string;
  /** "channel" covers Slack's public/private and any future source's shared
   *  space; "dm" covers 1:1 and group DMs. Kept coarse so a grouped view can
   *  render two kinds of section without knowing every source's internal
   *  taxonomy (Slack alone already has four: public, private, im, mpim). */
  kind: "channel" | "dm";
  fileCount: number;
  lastSynced: string | null;
  /** Present only when the last sync of this conversation failed. */
  errorHint: string | null;
}

export interface SourceConnection {
  connected: boolean;
  accountLabel: string | null;
}

export interface DocumentSource {
  id: DocumentSourceId;
  label: string;
  icon: LucideIcon;
  /** "available" = implemented and callable. "planned" = column renders a build-it-next state. */
  status: "available" | "planned";
  /** What this source will pull in once built — shown in the planned-state column. */
  plannedDescription?: string;
  getConnection?: () => Promise<SourceConnection>;
  fetchDocuments?: (
    params: { skip: number; limit: number; category?: string; conversationId?: string },
  ) => Promise<DocumentPage>;
  updateCategory?: (documentId: string, category: string) => Promise<unknown>;
  sendToScanner?: (documentId: string) => Promise<unknown>;
  retryDownload?: (documentId: string) => Promise<unknown>;
  /** Fetch a stored document's bytes for inline preview. Each source owns
   *  this because the id namespaces are per-service: handing an email
   *  attachment id to Slack's endpoint 404s, which is exactly what happened
   *  when the preview dialog called one connector's fetcher unconditionally. */
  fetchBlob?: (documentId: string) => Promise<Blob>;
  /** Absent for a source that has no notion of conversations. Reads only what
   *  a previous sync stored — no live API call — so it is cheap to poll. */
  fetchConversations?: () => Promise<{ conversations: UnifiedConversation[]; needsAttention: number }>;
  /** Live discovery — metadata only — so the sync picker has a complete list
   *  to choose from even before the first sync has ever run. Absent for a
   *  source with no notion of conversations. */
  refreshConversations?: () => Promise<{ conversations: UnifiedConversation[]; needsAttention: number }>;
  /** Remove a document. Every source implements this as a soft delete —
   *  hides it from FinPilot only, never touches Slack, the mailbox, or the
   *  stored bytes, and a later sync will not bring it back (each backend's
   *  own upsert never clears the deleted flag). Absent means the column
   *  shows no delete control. */
  deleteDocument?: (documentId: string) => Promise<void>;
  /** Accept a file from the browser. Only the browser-upload source has this
   *  — a connector's documents arrive by syncing, not by upload. */
  uploadDocument?: (file: File) => Promise<unknown>;
  /** Trigger a sync, optionally scoped to specific conversations and/or a date
   *  window. Absent for a source with no notion of a triggerable sync. */
  startSync?: (scope: {
    conversationIds?: string[];
    windowDays?: number | null;
  }) => Promise<{ sync_job_id: string; status: string; message: string }>;
}

function slackFileToDocument(file: SlackFile): UnifiedDocument {
  return {
    id: file.id,
    source: "slack",
    filename: file.filename,
    title: file.title,
    fileType: file.file_type,
    mimetype: file.mimetype,
    size: file.size,
    createdAt: file.created_at,
    category: file.category,
    categorySource: file.category_source,
    downloaded: file.downloaded,
    downloadFailed: file.download_failed,
    sharedBy: file.shared_by_user_name,
    externalUrl: file.slack_permalink || null,
    // The connector streams bytes through its own endpoint rather than handing
    // out an S3 URL, so previewing stays inside the company-scoped API.
    contentUrl: file.downloaded ? `/api/v1/slack/files/${file.id}/content` : null,
    scannerEligible: SCANNER_ELIGIBLE.has(file.category),
    conversationId: file.conversation_id,
  };
}

/** A short, human-readable type label. Slack supplies its own `pretty_type`
 *  ("PDF", "Word Document"); email has no equivalent, so derive one from the
 *  extension rather than from the mimetype. */
function fileTypeFromFilename(filename: string): string {
  const extension = filename.includes(".") ? filename.split(".").pop() ?? "" : "";
  return extension ? extension.toLowerCase() : "file";
}

function emailAttachmentToDocument(attachment: EmailAttachment): UnifiedDocument {
  return {
    id: attachment.id,
    source: "email",
    filename: attachment.filename,
    // An email attachment has no "title" of its own the way a Slack file
    // does — the message subject is the closest equivalent and is what
    // actually helps someone recognise the document.
    title: attachment.subject || null,
    // From the filename, not the mimetype: splitting a mimetype gives things
    // like "vnd.openxmlformats-officedocument.wordprocessingml.document",
    // which is what the UI would then show a user as the file's type.
    fileType: fileTypeFromFilename(attachment.filename),
    mimetype: attachment.mimetype,
    size: attachment.size,
    createdAt: attachment.received_at ?? attachment.created_at,
    category: attachment.category,
    categorySource: attachment.category_source,
    downloaded: attachment.downloaded,
    downloadFailed: attachment.download_failed,
    sharedBy: attachment.from_name || attachment.from_address,
    // No permalink: Gmail has no stable per-attachment public URL, and
    // linking to the message would need a different id than we store.
    externalUrl: null,
    contentUrl: attachment.downloaded ? `/api/v1/email/files/${attachment.id}/content` : null,
    scannerEligible: SCANNER_ELIGIBLE.has(attachment.category),
    conversationId: null,
  };
}

/** The Documents Service stores a compact lowercase enum; this page's filters
 *  use the connectors' Title Case labels. Mapped explicitly in both directions
 *  rather than assumed identical — the two vocabularies are owned by different
 *  services and only overlap partially (the service has no "Project Plans",
 *  the page has no "other"). Anything unmapped falls through to
 *  "uncategorized", which the page already knows how to display. */
const BROWSER_CATEGORY_TO_LABEL: Record<DocumentServiceCategory, string> = {
  invoices: "Invoices",
  receipts: "Receipts",
  reports: "Reports",
  contracts: "Contracts",
  images: "Images & Screenshots",
  // "Other" is an explicit choice — a browser upload always has a category,
  // so there is no automatic "couldn't classify it" fallback here the way
  // connectors have "uncategorized". Mapping this to that label would
  // conflate "a person said this isn't financial" with "the rules engine
  // couldn't guess," which are very different statements.
  other: "Other",
};

const LABEL_TO_BROWSER_CATEGORY: Record<string, DocumentServiceCategory> = Object.fromEntries(
  Object.entries(BROWSER_CATEGORY_TO_LABEL).map(([value, label]) => [label, value]),
) as Record<string, DocumentServiceCategory>;

function browserDocumentToDocument(doc: StoredDocument): UnifiedDocument {
  return {
    id: doc.id,
    source: "browser",
    filename: doc.filename,
    // A browser upload has no subject or Slack title to fall back on — the
    // filename is all the user gave us, and the UI already shows that.
    title: null,
    fileType: fileTypeFromFilename(doc.filename),
    mimetype: doc.mimetype,
    size: doc.size,
    createdAt: doc.created_at,
    category: BROWSER_CATEGORY_TO_LABEL[doc.category] ?? "Other",
    // The uploader picked it (or accepted the default) — there is no rules
    // engine behind this source, unlike the connectors' auto-categorisation.
    categorySource: "manual_override",
    // Storage is synchronous here: if the row exists, the bytes are stored.
    // There is no pending-download state to represent, unlike a connector
    // that discovers a file before fetching it.
    downloaded: true,
    downloadFailed: false,
    sharedBy: null,
    externalUrl: null,
    contentUrl: `/api/v1/documents/${doc.id}/content`,
    scannerEligible: SCANNER_ELIGIBLE.has(BROWSER_CATEGORY_TO_LABEL[doc.category] ?? "Other"),
    conversationId: null,
  };
}

function slackConversationToUnified(conv: SlackConversation): UnifiedConversation {
  return {
    id: conv.id,
    name: conv.name,
    kind: conv.conversation_type === "im" || conv.conversation_type === "mpim" ? "dm" : "channel",
    fileCount: conv.file_count,
    lastSynced: conv.last_synced,
    errorHint: conv.last_error_hint,
  };
}

export const DOCUMENT_SOURCES: DocumentSource[] = [
  {
    id: "slack",
    label: "Slack",
    icon: Slack,
    status: "available",
    getConnection: async () => {
      const status = await getSlackStatus();
      return { connected: status.connected, accountLabel: status.workspace_name ?? null };
    },
    fetchDocuments: async ({ skip, limit, category, conversationId }) => {
      // exactOptionalPropertyTypes rejects passing `category: undefined`
      // explicitly — the key must be entirely absent, not present-with-undefined.
      const response = await listSlackFiles({
        skip, limit,
        ...(category ? { category } : {}),
        ...(conversationId ? { conversationId } : {}),
      });
      return { documents: response.files.map(slackFileToDocument), total: response.total };
    },
    updateCategory: updateSlackFileCategory,
    sendToScanner: sendSlackFileToScanner,
    retryDownload: retrySlackFile,
    fetchBlob: fetchSlackFileBlob,
    deleteDocument: deleteSlackFile,
    fetchConversations: async () => {
      const response = await listSlackConversations();
      return {
        conversations: response.conversations.map(slackConversationToUnified),
        needsAttention: response.needs_attention,
      };
    },
    refreshConversations: async () => {
      const response = await refreshSlackConversations();
      return {
        conversations: response.conversations.map(slackConversationToUnified),
        needsAttention: response.needs_attention,
      };
    },
    startSync: startSlackSync,
  },
  {
    id: "email",
    label: "Email",
    icon: Mail,
    status: "available",
    getConnection: async () => {
      const status = await getEmailStatus();
      return { connected: status.connected, accountLabel: status.email_address ?? null };
    },
    fetchDocuments: async ({ skip, limit, category }) => {
      const response = await listEmailFiles({ skip, limit, ...(category ? { category } : {}) });
      return { documents: response.attachments.map(emailAttachmentToDocument), total: response.total };
    },
    updateCategory: updateEmailFileCategory,
    sendToScanner: sendEmailFileToScanner,
    startSync: startEmailSync,
    fetchBlob: fetchEmailFileBlob,
    retryDownload: retryEmailFile,
    deleteDocument: deleteEmailFile,
    // fetchConversations deliberately absent: a mailbox has no channel/DM
    // structure to group by, so SourceColumn keeps this source flat even in
    // grouped mode.
  },
  {
    id: "browser",
    label: "Browser Upload",
    icon: UploadCloud,
    status: "available",
    // No getConnection: there is nothing to connect. Files are here because
    // someone uploaded them, so the column is always "available" rather than
    // gated behind an OAuth state the way a connector is.
    fetchDocuments: async ({ skip, limit, category }) => {
      const mapped = category ? LABEL_TO_BROWSER_CATEGORY[category] : undefined;
      // A filter this source has no equivalent for (e.g. "Project Plans")
      // must return nothing rather than silently ignoring the filter and
      // showing everything — which would look like the filter was broken.
      if (category && !mapped) return { documents: [], total: 0 };
      const response = await listDocuments({ skip, limit, ...(mapped ? { category: mapped } : {}) });
      return { documents: response.documents.map(browserDocumentToDocument), total: response.total };
    },
    updateCategory: async (documentId, category) => {
      const mapped = LABEL_TO_BROWSER_CATEGORY[category];
      if (!mapped) throw new Error(`"${category}" is not a category this source supports`);
      return updateDocumentCategory(documentId, mapped);
    },
    sendToScanner: sendDocumentToScanner,
    fetchBlob: fetchDocumentBlob,
    deleteDocument,
    uploadDocument: (file: File) => uploadDocument(file),
    // No fetchConversations/startSync: nothing to group by and nothing to
    // sync — the user is the source.
  },
  {
    id: "whatsapp",
    label: "WhatsApp",
    icon: MessageCircle,
    status: "planned",
    plannedDescription:
      "Bills and receipts customers and vendors send over WhatsApp, including photos of paper receipts.",
  },
];

export function getSource(id: DocumentSourceId): DocumentSource | undefined {
  return DOCUMENT_SOURCES.find((source) => source.id === id);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const unit = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, unit)).toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

/** What the preview dialog can render inline. */
export type PreviewKind = "pdf" | "image" | "html" | "text" | "docx" | "unsupported";

export function previewKindFor(mimetype: string | null, filename: string): PreviewKind {
  const type = (mimetype ?? "").toLowerCase();
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";

  // Extension wins for HTML because Slack reports .html attachments as
  // text/plain. Trusting the mimetype alone showed an HTML invoice as a wall of
  // markup instead of the document it is.
  if (["html", "htm", "xhtml"].includes(extension) || type === "text/html") return "html";

  // No browser renders .docx natively, so this is converted to HTML in the
  // browser (mammoth) rather than handed to an iframe. Only the modern zipped
  // format is convertible — legacy .doc is a completely different binary
  // format mammoth cannot read, so it stays unsupported and offers a download.
  if (
    extension === "docx"
    || type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  ) {
    return "docx";
  }

  if (type === "application/pdf" || extension === "pdf") return "pdf";
  if (type.startsWith("image/")) return "image";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(extension)) return "image";
  if (type.startsWith("text/") || type === "application/json") return "text";
  if (["txt", "csv", "md", "json", "log", "xml"].includes(extension)) return "text";
  return "unsupported";
}

/** Broader-than-PreviewKind categorization for the file browser's icon
 *  views (FileTypeIcon) — "can this be rendered inline" and "what icon/
 *  color represents this file type" are different questions. A .zip is
 *  never previewable but still deserves its own icon, not the same blank
 *  "unsupported" one an .exe would get. */
export type FileKind = "pdf" | "word" | "excel" | "image" | "archive" | "audio" | "video" | "text" | "other";

export function fileKindFor(mimetype: string | null, filename: string): FileKind {
  const type = (mimetype ?? "").toLowerCase();
  const extension = filename.includes(".") ? filename.split(".").pop()!.toLowerCase() : "";

  if (type === "application/pdf" || extension === "pdf") return "pdf";
  if (type.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(extension)) return "image";
  if (
    ["doc", "docx"].includes(extension)
    || type.includes("wordprocessingml") || type === "application/msword"
  ) return "word";
  if (
    ["xls", "xlsx", "csv"].includes(extension)
    || type.includes("spreadsheetml") || type === "application/vnd.ms-excel" || type === "text/csv"
  ) return "excel";
  if (["zip", "rar", "7z", "tar", "gz"].includes(extension) || type.includes("zip")) return "archive";
  if (type.startsWith("audio/") || ["mp3", "wav", "m4a", "ogg"].includes(extension)) return "audio";
  if (type.startsWith("video/") || ["mp4", "mov", "avi", "webm"].includes(extension)) return "video";
  if (type.startsWith("text/") || ["txt", "md", "log", "json", "xml", "html", "htm"].includes(extension)) return "text";
  return "other";
}
