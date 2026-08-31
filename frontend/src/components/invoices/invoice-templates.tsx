import type { InvoiceTemplateKey, LogoPlacement } from "@/lib/settings-service";

/**
 * The four sales-invoice templates — kept here as the single source of
 * truth for both the live browser preview (app.invoices.tsx) and each
 * thumbnail below it. `invoice-service`'s own `sales_pdf.py` mirrors
 * these same four palettes for the actual downloaded PDF (ReportLab
 * can't share CSS with the browser, so the two are two implementations
 * of one design, not one shared file) — see that module's own docstring.
 */
export interface InvoiceTemplateStyle {
  key: InvoiceTemplateKey;
  label: string;
  description: string;
  pageBg: string;
  cardText: string;
  mutedText: string;
  accent: string;
  /** Text colour used *on* the accent-coloured table header row. */
  onAccent: string;
  ruleColor: string;
  zebraBg: string | null;
  fontFamily: string;
}

export const INVOICE_TEMPLATES: InvoiceTemplateStyle[] = [
  {
    key: "classic", label: "Classic", description: "Teal accent, light background — the FinPilot default.",
    pageBg: "#FFFFFF", cardText: "#111827", mutedText: "#6B7280", accent: "#0F766E", onAccent: "#FFFFFF",
    ruleColor: "#E5E7EB", zebraBg: "#F9FAFB", fontFamily: "inherit",
  },
  {
    key: "modern", label: "Modern", description: "Indigo accent, crisp geometric spacing.",
    pageBg: "#FFFFFF", cardText: "#111827", mutedText: "#6B7280", accent: "#4F46E5", onAccent: "#FFFFFF",
    ruleColor: "#E0E7FF", zebraBg: "#EEF2FF", fontFamily: "inherit",
  },
  {
    key: "midnight", label: "Midnight", description: "Dark background, warm gold accent — a premium feel.",
    pageBg: "#111827", cardText: "#F3F4F6", mutedText: "#9CA3AF", accent: "#F59E0B", onAccent: "#111827",
    ruleColor: "#374151", zebraBg: "#1F2937", fontFamily: "inherit",
  },
  {
    key: "minimal", label: "Minimal", description: "Black on white, hairline rules, no colour at all.",
    pageBg: "#FFFFFF", cardText: "#111827", mutedText: "#6B7280", accent: "#111827", onAccent: "#111827",
    ruleColor: "#111827", zebraBg: null, fontFamily: "inherit",
  },
];

const DEFAULT_TEMPLATE = INVOICE_TEMPLATES[0]!;

export function templateStyle(key: InvoiceTemplateKey): InvoiceTemplateStyle {
  return INVOICE_TEMPLATES.find((t) => t.key === key) ?? DEFAULT_TEMPLATE;
}

const LOGO_JUSTIFY: Record<LogoPlacement, string> = {
  left: "flex-start", center: "center", right: "flex-end",
};

/** A small, stylised representation of the template's look — not a
 *  scaled-down copy of the real preview (illegible at this size), the
 *  same reasoning a slide-deck theme picker shows an abstract sketch of
 *  a layout rather than shrinking real body text). */
export function InvoiceTemplateThumbnail({
  template, active, logoUrl, logoPlacement, onClick,
}: {
  template: InvoiceTemplateStyle;
  active: boolean;
  logoUrl: string | null;
  logoPlacement: LogoPlacement;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group flex flex-col gap-1.5 rounded-xl p-1.5 text-left transition ${
        active ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : "hover:bg-muted/50"
      }`}
      aria-pressed={active}
    >
      <div
        className="aspect-[4/3] w-full overflow-hidden rounded-lg border shadow-sm"
        style={{ backgroundColor: template.pageBg }}
      >
        <div className="flex h-full flex-col gap-1.5 p-2.5">
          <div className="flex items-center gap-1" style={{ justifyContent: LOGO_JUSTIFY[logoPlacement] }}>
            {logoUrl ? (
              <img src={logoUrl} alt="" className="h-3.5 w-3.5 rounded-sm object-contain" />
            ) : (
              <div className="h-2 w-6 rounded-sm" style={{ backgroundColor: template.accent }} />
            )}
          </div>
          <div className="h-1.5 w-10 rounded-full" style={{ backgroundColor: template.mutedText, opacity: 0.6 }} />
          <div className="mt-1 space-y-1">
            <div className="h-1 w-full rounded-full" style={{ backgroundColor: template.ruleColor }} />
            <div className="h-1 w-full rounded-full" style={{ backgroundColor: template.ruleColor }} />
            <div className="h-1 w-2/3 rounded-full" style={{ backgroundColor: template.ruleColor }} />
          </div>
          <div className="mt-auto flex justify-end">
            <div className="h-2 w-8 rounded-sm" style={{ backgroundColor: template.accent }} />
          </div>
        </div>
      </div>
      <div className="px-0.5">
        <p className="text-xs font-semibold">{template.label}</p>
        <p className="text-[10px] leading-tight text-muted-foreground">{template.description}</p>
      </div>
    </button>
  );
}
