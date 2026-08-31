import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Send, Tag, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import {
  DOCUMENT_CATEGORIES, getSource, type UnifiedDocument,
} from "@/lib/documents";

interface BulkResult {
  ok: number;
  failed: { filename: string; reason: string }[];
}

/**
 * Actions applied to a multi-document selection.
 *
 * Requests run one at a time rather than via Promise.all: the connectors sit
 * behind Slack's rate limiter, and firing 20 downloads-and-forwards at once is
 * the reliable way to get throttled. Sequential is slower but finishes; it also
 * lets the bar report honest progress and keep going past an individual failure
 * instead of aborting the whole batch on the first rejection.
 */
export function BulkActionsBar({
  selected,
  onClear,
  onDone,
}: {
  selected: UnifiedDocument[];
  onClear: () => void;
  onDone: (succeededIds: string[]) => void;
}) {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState<null | "scanner" | "category">(null);
  const [done, setDone] = useState(0);
  const [category, setCategory] = useState<string>("");

  const scannerTargets = selected.filter((doc) => doc.scannerEligible && doc.downloaded);
  const total = selected.length;

  function report(action: string, result: BulkResult) {
    if (result.failed.length === 0) {
      toast.success(`${action}: ${result.ok} document${result.ok === 1 ? "" : "s"}`);
      return;
    }
    if (result.ok === 0) {
      toast.error(`${action} failed for all ${result.failed.length} — ${result.failed[0]?.reason ?? ""}`);
      return;
    }
    // Partial outcomes are the common real-world case; say exactly what failed
    // rather than a bare "done" that hides it.
    toast.warning(
      `${action}: ${result.ok} succeeded, ${result.failed.length} failed — ` +
        `${result.failed.slice(0, 2).map((f) => f.filename).join(", ")}` +
        `${result.failed.length > 2 ? "…" : ""}`,
    );
  }

  async function runSequentially(
    documents: UnifiedDocument[],
    run: (doc: UnifiedDocument) => Promise<unknown>,
  ): Promise<{ result: BulkResult; succeededIds: string[] }> {
    const result: BulkResult = { ok: 0, failed: [] };
    const succeededIds: string[] = [];
    setDone(0);

    for (const doc of documents) {
      try {
        await run(doc);
        result.ok += 1;
        succeededIds.push(doc.id);
      } catch (error) {
        result.failed.push({
          filename: doc.filename,
          reason: (error as Error)?.message ?? "unknown error",
        });
      }
      setDone((n) => n + 1);
    }
    return { result, succeededIds };
  }

  async function sendToScanner() {
    if (scannerTargets.length === 0) return;
    setRunning("scanner");
    const { result, succeededIds } = await runSequentially(scannerTargets, (doc) => {
      const source = getSource(doc.source);
      if (!source?.sendToScanner) throw new Error(`${doc.source} cannot send to the scanner`);
      return source.sendToScanner(doc.id);
    });
    setRunning(null);
    report("Sent to AI Scanner", result);
    onDone(succeededIds);
  }

  async function applyCategory() {
    if (!category) return;
    setRunning("category");
    const { result, succeededIds } = await runSequentially(selected, (doc) => {
      const source = getSource(doc.source);
      if (!source?.updateCategory) throw new Error(`${doc.source} categories are read-only`);
      return source.updateCategory(doc.id, category);
    });
    setRunning(null);
    report(`Recategorised as ${category}`, result);
    queryClient.invalidateQueries({ queryKey: ["source-documents"] });
    onDone(succeededIds);
    setCategory("");
  }

  const busy = running !== null;
  const batchSize = running === "scanner" ? scannerTargets.length : total;

  return (
    <div className="sticky bottom-4 z-20 mx-auto w-full max-w-3xl">
      <div className="surface flex flex-col gap-3 border p-3 shadow-lg">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{total} selected</span>
            {scannerTargets.length !== total && (
              <span className="text-xs text-muted-foreground">
                ({scannerTargets.length} can go to the scanner)
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Select value={category} onValueChange={setCategory} disabled={busy}>
              <SelectTrigger className="h-9 w-44 rounded-xl text-xs">
                <SelectValue placeholder="Set category…" />
              </SelectTrigger>
              <SelectContent>
                {DOCUMENT_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 rounded-xl"
              onClick={applyCategory}
              disabled={busy || !category}
            >
              {running === "category" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Tag className="h-3.5 w-3.5" />}
              Apply
            </Button>

            <Button
              size="sm"
              className="gap-1.5 rounded-xl"
              onClick={sendToScanner}
              disabled={busy || scannerTargets.length === 0}
              title={
                scannerTargets.length === 0
                  ? "Only downloaded invoices, receipts and financial spreadsheets can be scanned"
                  : `Send ${scannerTargets.length} document(s) to the AI Scanner`
              }
            >
              {running === "scanner" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
              Send {scannerTargets.length > 0 ? scannerTargets.length : ""} to Scanner
            </Button>

            <Button size="sm" variant="ghost" className="gap-1.5" onClick={onClear} disabled={busy}>
              <X className="h-3.5 w-3.5" /> Clear
            </Button>
          </div>
        </div>

        {busy && (
          <div className="space-y-1">
            <Progress value={batchSize ? (done / batchSize) * 100 : 0} className="h-1.5" />
            <p className="text-xs text-muted-foreground">
              Processing {done} of {batchSize}… leaving this page will stop the remaining ones.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
