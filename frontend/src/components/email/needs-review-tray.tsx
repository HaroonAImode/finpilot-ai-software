import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Inbox, Loader2, RefreshCw, ShieldAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  approveEmailAttachment, listEmailFiles, rejectEmailAttachment, type EmailAttachment,
} from "@/lib/email-connector";
import { formatBytes } from "@/lib/documents";
import { toast } from "sonner";

type RuleScope = "none" | "address" | "domain";

/**
 * Attachments the sync found but did not import.
 *
 * These have metadata only — their bytes were never downloaded, which is the
 * entire point (docs/email-connector-plan.md §5b): if nobody approves them,
 * FinPilot never holds a copy. Approving fetches from Gmail on demand, so
 * there is a real network round-trip behind that button and it is worth
 * showing a spinner for.
 */
export function NeedsReviewTray() {
  const queryClient = useQueryClient();
  const [decidingId, setDecidingId] = useState<string | null>(null);
  /** Per-row choice of what standing rule the decision should also create. */
  const [ruleScope, setRuleScope] = useState<Record<string, RuleScope>>({});

  const reviewQuery = useQuery({
    queryKey: ["email-files", "needs_review"],
    queryFn: () => listEmailFiles({ reviewStatus: "needs_review", limit: 100 }),
  });

  function afterDecision() {
    // Both lists move: one row leaves the tray, and (on approve) joins the
    // imported set the Documents page reads.
    queryClient.invalidateQueries({ queryKey: ["email-files"] });
    queryClient.invalidateQueries({ queryKey: ["source-documents", "email"] });
    setDecidingId(null);
  }

  const approveMutation = useMutation({
    mutationFn: ({ id, scope }: { id: string; scope: RuleScope }) =>
      approveEmailAttachment(id, scope === "none" ? undefined : scope),
    onSuccess: () => toast.success("Imported — it now appears in Documents"),
    onError: (error: Error) => toast.error(error.message || "Could not import this attachment"),
    onSettled: afterDecision,
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, scope }: { id: string; scope: RuleScope }) =>
      rejectEmailAttachment(id, scope === "none" ? undefined : scope),
    onSuccess: () => toast.success("Rejected — it was never downloaded"),
    onError: (error: Error) => toast.error(error.message || "Could not reject this attachment"),
    onSettled: afterDecision,
  });

  const attachments = reviewQuery.data?.attachments ?? [];
  const busy = approveMutation.isPending || rejectMutation.isPending;

  function scopeFor(file: EmailAttachment): RuleScope {
    return ruleScope[file.id] ?? "none";
  }

  function domainOf(file: EmailAttachment): string | null {
    const address = file.from_address ?? "";
    return address.includes("@") ? address.split("@").pop() ?? null : null;
  }

  return (
    <section className="surface max-w-3xl p-6">
      <div className="flex items-start gap-4">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-muted">
          <ShieldAlert className="h-6 w-6 text-muted-foreground" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold">Needs review</h3>
            {attachments.length > 0 && (
              <Badge variant="outline">{attachments.length}</Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Attachments FinPilot found but wasn't confident enough to import. Nothing here has been
            downloaded — approving fetches it, rejecting leaves it in your mailbox untouched.
          </p>
        </div>
      </div>

      <div className="mt-5 space-y-3">
        {reviewQuery.isPending && (
          <div className="space-y-3" aria-busy="true" aria-label="Loading items to review">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="rounded-xl border p-4">
                <Skeleton className="h-4 w-2/5" />
                <Skeleton className="mt-2 h-3 w-3/5" />
              </div>
            ))}
          </div>
        )}

        {reviewQuery.isError && (
          <div className="flex flex-col items-center gap-2 rounded-xl border p-6 text-center">
            <p className="text-sm text-destructive">
              {(reviewQuery.error as Error)?.message ?? "Could not load the review queue."}
            </p>
            <Button variant="outline" size="sm" className="gap-1.5 rounded-xl" onClick={() => reviewQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" /> Try again
            </Button>
          </div>
        )}

        {reviewQuery.isSuccess && attachments.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-8 text-center">
            <Inbox className="h-7 w-7 text-muted-foreground" />
            <p className="text-sm font-medium">Nothing waiting</p>
            <p className="max-w-sm text-xs text-muted-foreground">
              Everything the last sync found was either confidently financial or came from a sender
              you allow-listed.
            </p>
          </div>
        )}

        {attachments.map((file) => {
          const domain = domainOf(file);
          const scope = scopeFor(file);
          const rowBusy = decidingId === file.id && busy;

          return (
            <div key={file.id} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium" title={file.filename}>{file.filename}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)}
                    {file.received_at ? ` · ${new Date(file.received_at).toLocaleDateString()}` : ""}
                    {file.from_address ? ` · from ${file.from_name || file.from_address}` : ""}
                  </p>
                  {file.subject && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground" title={file.subject}>
                      {file.subject}
                    </p>
                  )}
                </div>
                <Badge variant="outline" className="shrink-0 text-[11px] text-muted-foreground">
                  {file.review_reason === "low_confidence" ? "Not recognised as financial" : file.review_reason}
                </Badge>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {file.from_address && (
                  <Select
                    value={scope}
                    onValueChange={(value) =>
                      setRuleScope((prev) => ({ ...prev, [file.id]: value as RuleScope }))
                    }
                  >
                    <SelectTrigger
                      className="h-8 w-auto rounded-lg text-xs"
                      aria-label={`Also create a rule for ${file.from_address}`}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Just this one</SelectItem>
                      <SelectItem value="address">Always, from {file.from_address}</SelectItem>
                      {domain && <SelectItem value="domain">Always, from anyone @{domain}</SelectItem>}
                    </SelectContent>
                  </Select>
                )}

                <div className="ml-auto flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 rounded-lg"
                    disabled={rowBusy}
                    onClick={() => {
                      setDecidingId(file.id);
                      rejectMutation.mutate({ id: file.id, scope });
                    }}
                  >
                    <X className="h-3.5 w-3.5" /> Reject
                  </Button>
                  <Button
                    size="sm"
                    className="gap-1.5 rounded-lg"
                    disabled={rowBusy}
                    onClick={() => {
                      setDecidingId(file.id);
                      approveMutation.mutate({ id: file.id, scope });
                    }}
                  >
                    {rowBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    {rowBusy ? "Importing…" : "Import"}
                  </Button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
