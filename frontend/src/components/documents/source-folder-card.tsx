/**
 * One "folder" on the Documents landing view — Slack, Email, WhatsApp, or
 * Browser Upload, each its own tile the way a real file manager's top-level
 * folders are, rather than four columns of files all visible on one screen
 * at once (docs/superpowers/specs/2026-09-01-documents-folder-browser-design.md).
 *
 * The folder itself is drawn as an actual folder shape — the same two-tone
 * yellow silhouette Windows/macOS use — with the source's own icon (Slack,
 * an envelope, a cloud-upload glyph) badged on top, rather than an abstract
 * colored square. Recognizable at a glance as "a folder," which an icon
 * inside a plain rounded square is not.
 */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Plug } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { type DocumentSource } from "@/lib/documents";

function FolderGlyph({ children, muted = false }: { children?: React.ReactNode; muted?: boolean }) {
  return (
    <span className="relative grid h-16 w-20 place-items-center">
      <svg viewBox="0 0 64 52" className="absolute inset-0 h-full w-full drop-shadow-sm" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M4 8a4 4 0 0 1 4-4h13l6 6h29a4 4 0 0 1 4 4v6H4V8Z"
          fill={muted ? "var(--muted-foreground)" : "#F5B942"}
          opacity={muted ? 0.35 : 1}
        />
        <path
          d="M2 15a3 3 0 0 1 3-3h54a3 3 0 0 1 3 3v29a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V15Z"
          fill={muted ? "var(--muted-foreground)" : "#FCD34D"}
          opacity={muted ? 0.25 : 1}
        />
      </svg>
      <span className="relative mt-2 grid h-7 w-7 place-items-center rounded-lg bg-white/70 shadow-sm dark:bg-black/25">
        {children}
      </span>
    </span>
  );
}

export function SourceFolderCard({ source, onOpen }: { source: DocumentSource; onOpen: () => void }) {
  const Icon = source.icon;
  const disabled = source.status === "planned";

  const connectionQuery = useQuery({
    queryKey: ["source-connection", source.id],
    queryFn: () => source.getConnection!(),
    enabled: source.status === "available" && Boolean(source.getConnection),
  });
  const connected = source.getConnection ? (connectionQuery.data?.connected ?? false) : true;

  // A cheap count-only fetch (limit: 1) — the total comes back regardless
  // of how many rows are requested, so this never pulls real file data
  // just to show a number on a folder icon.
  const countQuery = useQuery({
    queryKey: ["source-documents", source.id, undefined],
    queryFn: () => source.fetchDocuments!({ skip: 0, limit: 1 }),
    enabled: source.status === "available" && Boolean(source.fetchDocuments) && connected,
  });

  return (
    <button
      type="button"
      onClick={disabled ? undefined : onOpen}
      disabled={disabled}
      className={`surface lift flex flex-col items-center gap-2 p-6 text-center transition-transform ${
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:-translate-y-0.5"
      }`}
    >
      <FolderGlyph muted={disabled}>
        <Icon className="h-4 w-4 text-foreground/70" />
      </FolderGlyph>

      <div>
        <p className="font-display text-sm font-semibold">{source.label}</p>
        {disabled ? (
          <p className="mt-0.5 text-xs text-muted-foreground">Coming next</p>
        ) : !source.getConnection ? (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {countQuery.data ? `${countQuery.data.total} file${countQuery.data.total === 1 ? "" : "s"}` : "—"}
          </p>
        ) : connectionQuery.isPending ? (
          <Skeleton className="mx-auto mt-1 h-3 w-16" />
        ) : connected ? (
          <p className="mt-0.5 flex items-center justify-center gap-1 text-xs text-success">
            <CheckCircle2 className="h-3 w-3" />
            {countQuery.data ? `${countQuery.data.total} file${countQuery.data.total === 1 ? "" : "s"}` : "Connected"}
          </p>
        ) : (
          <p className="mt-0.5 flex items-center justify-center gap-1 text-xs text-muted-foreground">
            <Plug className="h-3 w-3" /> Not connected
          </p>
        )}
      </div>
    </button>
  );
}
