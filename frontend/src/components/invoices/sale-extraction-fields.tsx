import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Sparkles } from "lucide-react";
import type { Invoice } from "@/lib/invoice-service";

/**
 * Presentational helpers for showing an extracted invoice's fields — a
 * duplicate of the equivalent private helpers inline in `app.scanner.tsx`,
 * not an import from there. Per the design spec
 * (docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md §8.2):
 * duplicated rather than extracted, specifically so `app.scanner.tsx`
 * itself is not touched by this feature — its own behavior must stay
 * byte-for-byte identical. If the duplication drifts noticeably over
 * time, extracting both call sites onto this module is a pure
 * no-behavior-change refactor worth doing then, not now.
 */

export function confidenceTone(confidence: number): string {
  if (confidence >= 0.75) return "text-success";
  if (confidence >= 0.4) return "text-warning-foreground dark:text-warning";
  return "text-destructive";
}

function confidenceDotTone(confidence: number): string {
  if (confidence >= 0.75) return "bg-success";
  if (confidence >= 0.4) return "bg-warning";
  return "bg-destructive";
}

/** Amounts are shown with the currency the rules engine actually detected
 *  on this document, never a default — see scanner.tsx's own formatAmount
 *  for the full reasoning. */
export function formatAmount(n: number | null, currency?: string | null): string {
  if (n === null) return "—";
  const amount = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${currency} ${amount}` : amount;
}

export function currencyOf(invoice: Invoice | undefined): string | null {
  const detected = invoice?.extracted_fields?.["currency"]?.value;
  return typeof detected === "string" && detected ? detected : null;
}

export function currencySuffix(currency: string | null): string {
  return currency ? ` (${currency})` : "";
}

/** Compact version of the auto-filled/confidence signal Field shows, for a
 *  summary row with no room for a text badge. Renders nothing when the
 *  field was never found. */
export function ConfidenceDot({ confidence }: { confidence?: number | undefined }) {
  if (confidence === undefined) return null;
  return (
    <span
      className={`inline-block h-1.5 w-1.5 rounded-full ${confidenceDotTone(confidence)}`}
      title={`Auto-filled — ${Math.round(confidence * 100)}% confidence`}
    />
  );
}

/** `confidence` present = the rules engine auto-filled this field. Absent
 *  = the document genuinely didn't have it, or the engine couldn't
 *  confidently read it — blank for a human to fill in, not a failure. */
export function Field({
  label, value, onChange, confidence, onActivate, onDeactivate,
}: {
  label: string; value: string; onChange: (v: string) => void; confidence?: number | undefined;
  onActivate?: () => void; onDeactivate?: () => void;
}) {
  const autoFilled = confidence !== undefined;
  return (
    <div className="space-y-1.5" onMouseEnter={onActivate} onMouseLeave={onDeactivate} onFocus={onActivate} onBlur={onDeactivate}>
      <div className="flex items-center justify-between">
        <Label className="text-xs text-muted-foreground">{label}</Label>
        {autoFilled ? (
          <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${confidenceTone(confidence)}`}>
            <Sparkles className="h-2.5 w-2.5" /> {Math.round(confidence * 100)}%
          </span>
        ) : (
          <span className="text-[10px] font-medium text-muted-foreground">Not found — fill manually</span>
        )}
      </div>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`rounded-xl ${autoFilled ? "border-success/30 bg-success/5" : ""}`}
      />
    </div>
  );
}

/** Human-readable labels for Invoice.extracted_fields' keys — an unmapped
 *  key falls back to its own de-underscored name. */
const DETECTED_FIELD_LABELS: Record<string, string> = {
  customer_name: "Customer",
  currency: "Currency",
  document_type: "Document Type",
  payment_status: "Payment Status",
  city: "City",
  country: "Country",
};

function titleCase(key: string): string {
  return key.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/** The read-only half of the extraction, beyond the correctable header
 *  fields. Renders nothing when none were found. */
export function DetectedDetails({ fields }: { fields: Invoice["extracted_fields"] }) {
  const entries = Object.entries(fields ?? {});
  if (entries.length === 0) return null;

  return (
    <>
      <h4 className="mt-6 text-sm font-semibold">Detected Details</h4>
      <p className="text-xs text-muted-foreground">Read-only — detected from the document, not editable here</p>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {entries.map(([key, field]) => (
          <div key={key} className="rounded-xl border bg-muted/30 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">
                {DETECTED_FIELD_LABELS[key] ?? titleCase(key)}
              </span>
              {field.status === "UNCERTAIN" && (
                <span className="shrink-0 text-[10px] font-medium text-warning">Uncertain</span>
              )}
            </div>
            <p className="truncate text-sm font-medium" title={String(field.value)}>{String(field.value)}</p>
          </div>
        ))}
      </div>
    </>
  );
}

/** Every other labelled field the rules engine found — the document's own
 *  printed label, not a normalised one. Hovering highlights the value's
 *  source region, same as a canonical field. */
export function OtherFields({
  fields, onActivate, onDeactivate,
}: {
  fields: Invoice["dynamic_fields"];
  onActivate: (f: Invoice["dynamic_fields"][number]) => void;
  onDeactivate: () => void;
}) {
  if (!fields || fields.length === 0) return null;

  return (
    <>
      <h4 className="mt-6 text-sm font-semibold">Other Fields on This Document</h4>
      <p className="text-xs text-muted-foreground">
        Detected automatically — hover to see where each came from
      </p>
      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {fields.map((f) => (
          <div
            key={f.key}
            className="rounded-xl border bg-muted/30 px-3 py-2 transition-colors hover:border-primary/40"
            onMouseEnter={() => onActivate(f)}
            onMouseLeave={onDeactivate}
          >
            <span className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{f.label}</span>
            <p className="truncate text-sm font-medium" title={f.value}>{f.value}</p>
          </div>
        ))}
      </div>
    </>
  );
}
