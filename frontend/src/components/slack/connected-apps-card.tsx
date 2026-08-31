import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, RefreshCw, Slack, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  getSlackAuthorizeUrl,
  disconnectSlack,
  getSlackStatus,
  getSyncStatus,
} from "@/lib/slack-connector";
import { getSource } from "@/lib/documents";
import { SyncPickerDialog } from "@/components/slack/sync-picker-dialog";
import { toast } from "sonner";

const slackSource = getSource("slack")!;

export function ConnectedAppsCard({ onBrowseFiles }: { onBrowseFiles: () => void }) {
  const queryClient = useQueryClient();
  const [activeSyncId, setActiveSyncId] = useState<string | null>(null);

  const statusQuery = useQuery({ queryKey: ["slack-status"], queryFn: getSlackStatus });

  const syncStatusQuery = useQuery({
    queryKey: ["slack-sync", activeSyncId],
    queryFn: () => getSyncStatus(activeSyncId as string),
    enabled: Boolean(activeSyncId),
    refetchInterval: (query) => (query.state.data?.status === "running" || query.state.data?.status === "queued" ? 2000 : false),
  });

  useEffect(() => {
    if (syncStatusQuery.data?.status === "completed") {
      toast.success(`Sync complete — ${syncStatusQuery.data.files_discovered} files discovered`);
      setActiveSyncId(null);
    } else if (syncStatusQuery.data?.status === "failed") {
      toast.error("Slack sync failed — check the file list for partial results");
      setActiveSyncId(null);
    }
  }, [syncStatusQuery.data?.status]);

  const disconnectMutation = useMutation({
    mutationFn: disconnectSlack,
    onSuccess: () => {
      toast.success("Slack disconnected");
      queryClient.invalidateQueries({ queryKey: ["slack-status"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not disconnect Slack"),
  });

  // /connect is company-scoped, so it needs the bearer token — which a plain
  // browser navigation cannot carry. Fetch the URL, then navigate to it.
  const connectMutation = useMutation({
    mutationFn: getSlackAuthorizeUrl,
    onSuccess: (authorizeUrl) => {
      window.location.href = authorizeUrl;
    },
    onError: (error: Error) =>
      toast.error(error.message || "Could not start the Slack connection"),
  });

  // isPending (not isLoading) covers every "no data yet" state — first load, retry
  // backoff, and paused fetches. Using isLoading here let those middle states fall
  // through to the not-connected card, which wrongly implies Slack is disconnected
  // when the truth is simply unknown.
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

  // A failed status check is not the same as "not connected" — showing the connect
  // button here would invite a pointless OAuth round-trip against a down service.
  if (statusQuery.isError) {
    return (
      <section className="surface max-w-3xl p-6">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-destructive/10 text-destructive">
            <AlertCircle className="h-6 w-6" />
          </span>
          <div>
            <h3 className="text-base font-semibold">Slack connection status unavailable</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {(statusQuery.error as Error)?.message
                ?? "The Slack connector service did not respond."}{" "}
              Make sure it is running on port 8010.
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
            <Slack className="h-6 w-6" />
          </span>
          <div>
            <h3 className="text-base font-semibold">Slack</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Connect your Slack workspace to discover invoices, receipts and financial documents shared in
              conversations — then send them straight into the AI Scanner.
            </p>
            <Button
              className="mt-4 gap-2 rounded-xl"
              onClick={() => connectMutation.mutate()}
              disabled={connectMutation.isPending}
            >
              {connectMutation.isPending && <RefreshCw className="h-4 w-4 animate-spin" />}
              {connectMutation.isPending ? "Opening Slack…" : "Connect Slack"}
            </Button>
          </div>
        </div>
      </section>
    );
  }

  const isSyncing = syncStatusQuery.data?.status === "running" || syncStatusQuery.data?.status === "queued";

  return (
    <section className="surface max-w-3xl p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
            <Slack className="h-6 w-6" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{statusQuery.data.workspace_name ?? "Slack workspace"}</h3>
              <Badge variant="outline" className="gap-1 text-success">
                <CheckCircle2 className="h-3 w-3" /> Connected
              </Badge>
            </div>
            {statusQuery.data.installed_at && (
              <p className="mt-1 text-xs text-muted-foreground">
                Connected {new Date(statusQuery.data.installed_at).toLocaleDateString()}
              </p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              Scopes: {(statusQuery.data.scopes ?? []).join(", ")}
            </p>
          </div>
        </div>
        {/* Disconnect revokes the stored workspace token and drops the installation —
            not something to fire on a single stray click. */}
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
              <AlertDialogTitle>Disconnect Slack?</AlertDialogTitle>
              <AlertDialogDescription>
                This revokes FinPilot's access to{" "}
                {statusQuery.data.workspace_name ?? "your Slack workspace"} and removes the stored
                token. Files already synced stay in FinPilot, but no new files will be discovered
                until you reconnect.
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
          source={slackSource}
          onSyncStarted={(syncJobId) => setActiveSyncId(syncJobId)}
          trigger={
            <Button
              variant="outline"
              className="gap-2 rounded-xl"
              disabled={isSyncing}
            >
              <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
              {isSyncing ? `Syncing… ${syncStatusQuery.data?.files_discovered ?? 0} files found` : "Sync now"}
            </Button>
          }
        />
        <Button className="rounded-xl" onClick={onBrowseFiles}>
          Browse synced files
        </Button>
      </div>

      {isSyncing && syncStatusQuery.data && (
        <p className="mt-3 text-xs text-muted-foreground">
          {syncStatusQuery.data.conversations_processed} of{" "}
          {syncStatusQuery.data.total_conversations || "…"} conversations scanned ·{" "}
          {syncStatusQuery.data.files_downloaded} downloaded
          {syncStatusQuery.data.files_failed > 0
            ? ` · ${syncStatusQuery.data.files_failed} failed`
            : ""}
        </p>
      )}
    </section>
  );
}
