import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Hash, Loader2, RefreshCw, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { type DocumentSource, type UnifiedConversation } from "@/lib/documents";

const WINDOW_OPTIONS: { label: string; value: number | null }[] = [
  { label: "Last 24 hours", value: 1 },
  { label: "Last week", value: 7 },
  { label: "Last month", value: 30 },
  { label: "All time", value: null },
];

/**
 * "Sync now" for sources that support scoping: pick specific channels/DMs,
 * optionally limit how far back to look, then trigger the run.
 *
 * Opens the same picker from wherever a source's files appear — Connected
 * Apps and the Documents page both render this around their own trigger
 * button, so the scope choice is never a one-off available in only one place.
 */
export function SyncPickerDialog({
  source,
  trigger,
  onSyncStarted,
}: {
  source: DocumentSource;
  trigger: React.ReactNode;
  onSyncStarted?: (syncJobId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [windowDays, setWindowDays] = useState<number | null>(null);

  // Two independent capabilities. A source that can sync but has nothing to
  // pick *from* (email — a mailbox has no channel/DM structure) still gets a
  // working dialog, just with the date window alone. Gating the whole dialog
  // on the conversation picker would render the trigger as a dead button.
  const canSync = Boolean(source.startSync);
  const hasConversationPicker = Boolean(source.refreshConversations);

  const conversationsQuery = useQuery({
    queryKey: ["sync-picker-conversations", source.id],
    queryFn: () => source.refreshConversations!(),
    enabled: open && hasConversationPicker,
    staleTime: 0, // always re-discover live on open, never show a stale picker
    retry: false,
  });

  useEffect(() => {
    if (!open) {
      // Reset only after the close animation would have finished reading it —
      // stale selections must never leak into the next time this opens.
      setSelected(new Set());
      setWindowDays(null);
    }
  }, [open]);

  const startSyncMutation = useMutation({
    mutationFn: () =>
      // exactOptionalPropertyTypes rejects `conversationIds: undefined` — the
      // key must be entirely absent (meaning "no restriction"), not
      // present-with-undefined.
      source.startSync!({
        ...(selected.size > 0 ? { conversationIds: Array.from(selected) } : {}),
        windowDays,
      }),
    onSuccess: (data) => {
      toast(`Sync started — discovering files from ${source.label}…`);
      onSyncStarted?.(data.sync_job_id);
      setOpen(false);
    },
    onError: (error: Error) => toast.error(error.message || "Could not start sync"),
  });

  const conversations = conversationsQuery.data?.conversations ?? [];
  const channels = conversations.filter((c) => c.kind === "channel");
  const dms = conversations.filter((c) => c.kind === "dm");

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleGroup(group: UnifiedConversation[], checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const conv of group) {
        if (checked) next.add(conv.id);
        else next.delete(conv.id);
      }
      return next;
    });
  }

  if (!canSync) return <>{trigger}</>;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Choose what to sync</DialogTitle>
          <DialogDescription>
            {hasConversationPicker
              ? "Pick specific channels or DMs to pull files from, or leave nothing selected to sync everything FinPilot can reach."
              : `Pull invoices and receipts from ${source.label}. Choose how far back to look.`}
          </DialogDescription>
        </DialogHeader>

        {hasConversationPicker && conversationsQuery.isPending && (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Discovering channels and DMs…
          </div>
        )}

        {hasConversationPicker && conversationsQuery.isError && (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <p className="text-sm text-destructive">
              {(conversationsQuery.error as Error)?.message ?? `Could not reach ${source.label}.`}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 rounded-xl"
              onClick={() => conversationsQuery.refetch()}
            >
              <RefreshCw className="h-3.5 w-3.5" /> Retry
            </Button>
          </div>
        )}

        {hasConversationPicker && conversationsQuery.isSuccess && (
          <ScrollArea className="h-64 rounded-xl border">
            <div className="space-y-4 p-3">
              {channels.length === 0 && dms.length === 0 && (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No channels or DMs found. Invite FinPilot to a channel in Slack, then retry.
                </p>
              )}

              {channels.length > 0 && (
                <ConversationGroupList
                  label="Channels"
                  icon={Hash}
                  group={channels}
                  selected={selected}
                  onToggle={toggle}
                  onToggleGroup={toggleGroup}
                />
              )}

              {dms.length > 0 && (
                <ConversationGroupList
                  label="Direct messages"
                  icon={User}
                  group={dms}
                  selected={selected}
                  onToggle={toggle}
                  onToggleGroup={toggleGroup}
                />
              )}
            </div>
          </ScrollArea>
        )}

        <div className="space-y-2">
          <p className="text-sm font-medium">How far back?</p>
          <RadioGroup
            value={String(windowDays)}
            onValueChange={(value) => setWindowDays(value === "null" ? null : Number(value))}
            className="grid grid-cols-2 gap-2"
          >
            {WINDOW_OPTIONS.map((option) => (
              <label
                key={option.label}
                className="flex cursor-pointer items-center gap-2 rounded-lg border p-2 text-sm has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5"
              >
                <RadioGroupItem value={String(option.value)} id={`window-${option.label}`} />
                {option.label}
              </label>
            ))}
          </RadioGroup>
        </div>

        <DialogFooter>
          <Button variant="outline" className="rounded-xl" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            className="gap-2 rounded-xl"
            onClick={() => startSyncMutation.mutate()}
            disabled={
              startSyncMutation.isPending || (hasConversationPicker && conversationsQuery.isPending)
            }
          >
            {startSyncMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {selected.size > 0 ? `Extract now (${selected.size} selected)` : "Extract now"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConversationGroupList({
  label,
  icon: Icon,
  group,
  selected,
  onToggle,
  onToggleGroup,
}: {
  label: string;
  icon: typeof Hash;
  group: UnifiedConversation[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleGroup: (group: UnifiedConversation[], checked: boolean) => void;
}) {
  const allSelected = group.every((c) => selected.has(c.id));

  return (
    <div>
      <label className="mb-1.5 flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Checkbox
          checked={allSelected}
          onCheckedChange={(checked) => onToggleGroup(group, checked === true)}
          aria-label={`Select all ${label.toLowerCase()}`}
        />
        {label}
      </label>
      <div className="space-y-1">
        {group.map((conv) => (
          <Label
            key={conv.id}
            htmlFor={`conv-${conv.id}`}
            className="flex cursor-pointer items-center gap-2.5 rounded-lg p-2 text-sm hover:bg-muted/40"
          >
            <Checkbox
              id={`conv-${conv.id}`}
              checked={selected.has(conv.id)}
              onCheckedChange={() => onToggle(conv.id)}
            />
            <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-normal" title={conv.name}>
              {conv.name}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {conv.fileCount} file{conv.fileCount === 1 ? "" : "s"}
            </span>
            {conv.errorHint && (
              <Badge variant="outline" className="shrink-0 gap-1 text-[10px] text-destructive">
                <AlertTriangle className="h-3 w-3" />
              </Badge>
            )}
          </Label>
        ))}
      </div>
    </div>
  );
}
