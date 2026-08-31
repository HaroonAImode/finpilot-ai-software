import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Mail, RefreshCw, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { getEmailAuthorizeUrl, disconnectEmail, getEmailStatus, getEmailSyncStatus } from "@/lib/email-connector";
import { getSource } from "@/lib/documents";
import { SyncPickerDialog } from "@/components/slack/sync-picker-dialog";
import { toast } from "sonner";
import { useEffect, useState } from "react";

const emailSource = getSource("email")!;

/**
 * Email's Connected Apps card — Phase 1 (connect/status/disconnect only).
 *
 * Deliberately smaller than Slack's ConnectedAppsCard: there is no "Sync now"
 * or "Browse synced files" here yet, because message sync and attachment
 * download are Phase 2 (see docs/email-connector-plan.md) and do not exist on
 * the backend. Once they do, this gains the same sync-status polling and
 * picker dialog Slack's card has — the shape is intentionally left room for
 * that rather than needing a rewrite.
 */
export function EmailConnectedAppsCard({ onBrowseFiles }: { onBrowseFiles?: () => void } = {}) {
  const queryClient = useQueryClient();
  const [activeSyncId, setActiveSyncId] = useState<string | null>(null);

  const statusQuery = useQuery({ queryKey: ["email-status"], queryFn: getEmailStatus });

  const syncStatusQuery = useQuery({
    queryKey: ["email-sync", activeSyncId],
    queryFn: () => getEmailSyncStatus(activeSyncId as string),
    enabled: Boolean(activeSyncId),
    refetchInterval: (query) =>
      query.state.data?.status === "running" || query.state.data?.status === "queued" ? 2000 : false,
  });

  useEffect(() => {
    if (syncStatusQuery.data?.status === "completed") {
      const job = syncStatusQuery.data;
      toast.success(
        `Sync complete — ${job.attachments_downloaded} of ${job.attachments_discovered} attachments imported`,
      );
      setActiveSyncId(null);
      queryClient.invalidateQueries({ queryKey: ["email-status"] });
      queryClient.invalidateQueries({ queryKey: ["source-documents", "email"] });
    } else if (syncStatusQuery.data?.status === "failed") {
      toast.error("Email sync failed — check the file list for partial results");
      setActiveSyncId(null);
    }
  }, [syncStatusQuery.data?.status]);

  const disconnectMutation = useMutation({
    mutationFn: disconnectEmail,
    onSuccess: () => {
      toast.success("Mailbox disconnected");
      queryClient.invalidateQueries({ queryKey: ["email-status"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not disconnect the mailbox"),
  });

  // /connect is company-scoped, so it needs the bearer token — which a plain
  // browser navigation cannot carry. Fetch the URL, then navigate to it.
  const connectMutation = useMutation({
    mutationFn: getEmailAuthorizeUrl,
    onSuccess: (authorizeUrl) => {
      window.location.href = authorizeUrl;
    },
    onError: (error: Error, provider) =>
      toast.error(
        error.message ||
          `Could not start the ${provider === "outlook" ? "Microsoft" : "Google"} connection`,
      ),
  });

  if (statusQuery.isPending) {
    return (
      <section className="surface max-w-3xl p-6">
        <div className="flex items-start gap-4">
          <Skeleton className="h-12 w-12 shrink-0 rounded-2xl" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-full max-w-md" />
            <Skeleton className="h-9 w-32 rounded-xl" />
          </div>
        </div>
      </section>
    );
  }

  if (statusQuery.isError) {
    return (
      <section className="surface max-w-3xl p-6">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-destructive/10 text-destructive">
            <AlertCircle className="h-6 w-6" />
          </span>
          <div>
            <h3 className="text-base font-semibold">Email connection status unavailable</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {(statusQuery.error as Error)?.message
                ?? "The Email connector service did not respond."}{" "}
              Make sure it is running on port 8011.
            </p>
            <Button
              variant="outline"
              className="mt-4 gap-1.5 rounded-xl"
              onClick={() => statusQuery.refetch()}
            >
              <RefreshCw className="h-4 w-4" /> Retry
            </Button>
          </div>
        </div>
      </section>
    );
  }

  if (!statusQuery.data?.connected) {
    return (
      <section className="surface max-w-3xl p-6">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
            <Mail className="h-6 w-6" />
          </span>
          <div>
            <h3 className="text-base font-semibold">Email</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Connect a mailbox to discover invoices and receipts arriving as attachments, and pull
              them straight into FinPilot.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                className="gap-2 rounded-xl"
                onClick={() => connectMutation.mutate("gmail")}
                disabled={connectMutation.isPending}
              >
                {connectMutation.isPending && connectMutation.variables === "gmail" && (
                  <RefreshCw className="h-4 w-4 animate-spin" />
                )}
                {connectMutation.isPending && connectMutation.variables === "gmail"
                  ? "Opening Google…"
                  : "Connect Gmail"}
              </Button>
              {/* Outlook (plan §11a) is fully built server-side but only live
                  once an Azure AD App Registration is provisioned — until
                  then this surfaces the connector's own clear 503 as a toast
                  rather than pretending the button isn't here. */}
              <Button
                variant="outline"
                className="gap-2 rounded-xl"
                onClick={() => connectMutation.mutate("outlook")}
                disabled={connectMutation.isPending}
              >
                {connectMutation.isPending && connectMutation.variables === "outlook" && (
                  <RefreshCw className="h-4 w-4 animate-spin" />
                )}
                {connectMutation.isPending && connectMutation.variables === "outlook"
                  ? "Opening Microsoft…"
                  : "Connect Outlook"}
              </Button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  const needsReauth = statusQuery.data.needs_reauth;
  const isSyncing =
    syncStatusQuery.data?.status === "running" || syncStatusQuery.data?.status === "queued";

  return (
    <section className="surface max-w-3xl p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
            <Mail className="h-6 w-6" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{statusQuery.data.email_address ?? "Gmail account"}</h3>
              {needsReauth ? (
                <Badge variant="outline" className="gap-1 text-destructive">
                  <AlertCircle className="h-3 w-3" /> Needs reconnecting
                </Badge>
              ) : (
                <Badge variant="outline" className="gap-1 text-success">
                  <CheckCircle2 className="h-3 w-3" /> Connected
                </Badge>
              )}
            </div>
            {statusQuery.data.connected_at && (
              <p className="mt-1 text-xs text-muted-foreground">
                Connected {new Date(statusQuery.data.connected_at).toLocaleDateString()}
              </p>
            )}
            {statusQuery.data.last_synced_at && (
              <p className="mt-1 text-xs text-muted-foreground">
                Last synced {new Date(statusQuery.data.last_synced_at).toLocaleString()}
              </p>
            )}
          </div>
        </div>
        {/* Disconnecting revokes stored tokens outright (see the service's
            disconnect docstring) — a mailbox holds more sensitive material
            than a Slack workspace, so this is not something to fire on a
            single stray click. */}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-muted-foreground"
              disabled={disconnectMutation.isPending}
            >
              <Unplug className="h-3.5 w-3.5" />
              {disconnectMutation.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Disconnect this mailbox?</AlertDialogTitle>
              <AlertDialogDescription>
                This revokes FinPilot's access to{" "}
                {statusQuery.data.email_address ?? "this Google account"} and permanently deletes
                the stored connection — unlike disconnecting Slack, nothing is kept.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel className="rounded-xl">Cancel</AlertDialogCancel>
              <AlertDialogAction className="rounded-xl" onClick={() => disconnectMutation.mutate()}>
                Disconnect
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        <SyncPickerDialog
          source={emailSource}
          onSyncStarted={(syncJobId) => setActiveSyncId(syncJobId)}
          trigger={
            <Button variant="outline" className="gap-2 rounded-xl" disabled={isSyncing}>
              <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
              {isSyncing
                ? `Syncing… ${syncStatusQuery.data?.attachments_discovered ?? 0} found`
                : "Sync now"}
            </Button>
          }
        />
        {onBrowseFiles && (
          <Button className="rounded-xl" onClick={onBrowseFiles}>
            Browse synced attachments
          </Button>
        )}
      </div>

      {isSyncing && syncStatusQuery.data && (
        <p className="mt-3 text-xs text-muted-foreground">
          {syncStatusQuery.data.messages_scanned} messages scanned ·{" "}
          {syncStatusQuery.data.attachments_downloaded} imported
          {syncStatusQuery.data.attachments_failed > 0
            ? ` · ${syncStatusQuery.data.attachments_failed} failed`
            : ""}
        </p>
      )}

      {needsReauth && (
        <p className="mt-3 rounded-lg bg-destructive/5 px-3 py-2 text-xs text-destructive">
          Google access expired or was revoked. Reconnect to keep this working.
        </p>
      )}
    </section>
  );
}
