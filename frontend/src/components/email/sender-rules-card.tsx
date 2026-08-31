import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  createSenderRule, deleteSenderRule, listSenderRules,
} from "@/lib/email-connector";
import { toast } from "sonner";

/**
 * Manage the allow/deny lists that decide which senders' attachments are
 * imported without asking.
 *
 * Most rules get created from the review tray rather than typed in here —
 * this card is for seeing the whole set and correcting it.
 */
export function SenderRulesCard() {
  const queryClient = useQueryClient();
  const [pattern, setPattern] = useState("");
  const [action, setAction] = useState<"allow" | "deny">("allow");

  const rulesQuery = useQuery({ queryKey: ["email-sender-rules"], queryFn: listSenderRules });

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["email-sender-rules"] });
  }

  const createMutation = useMutation({
    mutationFn: () => createSenderRule(pattern.trim(), action),
    onSuccess: () => {
      toast.success("Rule saved");
      setPattern("");
      refresh();
    },
    onError: (error: Error) => toast.error(error.message || "Could not save this rule"),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteSenderRule,
    onSuccess: () => {
      toast.success("Rule removed");
      refresh();
    },
    onError: (error: Error) => toast.error(error.message || "Could not remove this rule"),
  });

  const rules = rulesQuery.data ?? [];
  const allowRules = rules.filter((r) => r.action === "allow");
  const denyRules = rules.filter((r) => r.action === "deny");

  return (
    <section className="surface max-w-3xl p-6">
      <h3 className="text-base font-semibold">Sender rules</h3>
      <p className="mt-1 text-sm text-muted-foreground">
        Always import from a trusted sender, or never import from a noisy one. Enter a full address
        (<span className="font-mono text-xs">billing@vendor.com</span>) or a whole domain
        (<span className="font-mono text-xs">vendor.com</span>). If two rules disagree, the deny wins.
      </p>

      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (pattern.trim()) createMutation.mutate();
        }}
      >
        <Input
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          placeholder="billing@vendor.com or vendor.com"
          className="min-w-[16rem] flex-1 rounded-xl"
          aria-label="Sender address or domain"
        />
        <Select value={action} onValueChange={(value) => setAction(value as "allow" | "deny")}>
          <SelectTrigger className="w-36 rounded-xl" aria-label="Rule action">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="allow">Always import</SelectItem>
            <SelectItem value="deny">Never import</SelectItem>
          </SelectContent>
        </Select>
        <Button
          type="submit"
          className="gap-1.5 rounded-xl"
          disabled={!pattern.trim() || createMutation.isPending}
        >
          {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          Add
        </Button>
      </form>

      <div className="mt-5 space-y-4">
        {rulesQuery.isPending && (
          <div className="space-y-2" aria-busy="true" aria-label="Loading sender rules">
            <Skeleton className="h-9 w-full rounded-lg" />
            <Skeleton className="h-9 w-3/4 rounded-lg" />
          </div>
        )}

        {rulesQuery.isError && (
          <div className="flex flex-col items-center gap-2 rounded-xl border p-6 text-center">
            <p className="text-sm text-destructive">
              {(rulesQuery.error as Error)?.message ?? "Could not load sender rules."}
            </p>
            <Button variant="outline" size="sm" className="gap-1.5 rounded-xl" onClick={() => rulesQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" /> Try again
            </Button>
          </div>
        )}

        {rulesQuery.isSuccess && rules.length === 0 && (
          <p className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">
            No rules yet. You can add one above, or build them up as you go by choosing
            “Always/Never from this sender” in the review queue.
          </p>
        )}

        {[
          { label: "Always import", items: allowRules, icon: CheckCircle2, tone: "text-success" },
          { label: "Never import", items: denyRules, icon: Ban, tone: "text-destructive" },
        ]
          .filter((group) => group.items.length > 0)
          .map(({ label, items, icon: Icon, tone }) => (
            <div key={label}>
              <p className={`mb-1.5 flex items-center gap-1.5 text-xs font-medium ${tone}`}>
                <Icon className="h-3.5 w-3.5" /> {label}
              </p>
              <div className="space-y-1">
                {items.map((rule) => (
                  <div key={rule.id} className="flex items-center gap-2 rounded-lg border px-3 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs" title={rule.pattern}>
                      {rule.pattern}
                    </span>
                    {!rule.pattern.includes("@") && (
                      <Badge variant="outline" className="shrink-0 text-[10px]">whole domain</Badge>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0 text-muted-foreground"
                      aria-label={`Remove rule for ${rule.pattern}`}
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(rule.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          ))}
      </div>
    </section>
  );
}
