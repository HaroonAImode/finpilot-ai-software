import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download, FileDown, ImageUp, Loader2, Plus, Printer, Trash2, Wallet, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import {
  createSalesInvoice, fetchSalesInvoicePdf, listSalesInvoices,
  type SalesInvoice, type SalesInvoiceCreatePayload,
} from "@/lib/invoice-service";
import {
  getCompanyProfile, updateCompanyProfile, type InvoiceTemplateKey, type LogoPlacement,
} from "@/lib/settings-service";
import { INVOICE_TEMPLATES, InvoiceTemplateThumbnail, templateStyle } from "@/components/invoices/invoice-templates";

export const Route = createFileRoute("/app/invoices")({
  head: () => ({
    meta: [
      { title: "Invoice Generator — FinPilot AI" },
      {
        name: "description",
        content:
          "Create sales invoices with live preview, automatic sales tax and PKR totals for Pakistani SMEs.",
      },
      { property: "og:title", content: "Invoice Generator — FinPilot AI" },
      {
        property: "og:description",
        content: "Build, preview and download professional PKR invoices with automatic tax calculation.",
      },
    ],
  }),
  component: InvoiceGenerator,
});

type DraftItem = { id: number; desc: string; qty: number; rate: number };

//: A logo this size already produces a comfortably large data: URI —
//  see settings-service's own Company.logo_url docstring for why this
//  is a data URI at all rather than a hosted file.
const MAX_LOGO_BYTES = 800_000;

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

function moneyOrDash(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : money(n);
}

/** Fields mirror exactly what the backend persists and prints on the PDF
 *  (app/services/sales_pdf.py) — no due date, discount, or notes, because
 *  none of those exist on the stored record and showing them here would
 *  promise something the downloaded document does not actually contain. */
function blankForm() {
  return {
    customerName: "",
    invoiceNumber: "",
    invoiceDate: "",
    ntn: "",
    taxRatePercent: "0",
  };
}

async function downloadPdf(invoiceId: string, filename: string) {
  const blob = await fetchSalesInvoicePdf(invoiceId);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read this file"));
    reader.readAsDataURL(file);
  });
}

const LOGO_JUSTIFY: Record<LogoPlacement, string> = {
  left: "flex-start", center: "center", right: "flex-end",
};

function InvoiceGenerator() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(blankForm());
  const [items, setItems] = useState<DraftItem[]>([{ id: 1, desc: "", qty: 1, rate: 0 }]);
  const [saved, setSaved] = useState<SalesInvoice | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const logoInputRef = useRef<HTMLInputElement>(null);

  // Branding — a company-wide preference (Settings Service), not a
  // per-invoice field: most businesses want every invoice to carry the
  // same letterhead. Local state mirrors the server value so a thumbnail
  // click or upload feels instant while the save happens in the
  // background — see the two mutations below.
  const companyQuery = useQuery({ queryKey: ["settings-company"], queryFn: getCompanyProfile });
  const [template, setTemplate] = useState<InvoiceTemplateKey>("classic");
  const [logoPlacement, setLogoPlacement] = useState<LogoPlacement>("left");
  const [logoUrl, setLogoUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!companyQuery.data) return;
    setTemplate(companyQuery.data.invoice_template);
    setLogoPlacement(companyQuery.data.logo_placement);
    setLogoUrl(companyQuery.data.logo_url);
  }, [companyQuery.data]);

  const brandingMutation = useMutation({
    mutationFn: updateCompanyProfile,
    onSuccess: (profile) => queryClient.setQueryData(["settings-company"], profile),
    onError: (error: Error) => toast.error(error.message),
  });

  const listQuery = useQuery({
    queryKey: ["invoices", "sales"],
    queryFn: () => listSalesInvoices({ limit: 50 }),
  });

  const subtotal = items.reduce((s, i) => s + i.qty * i.rate, 0);
  const taxRate = (Number(form.taxRatePercent) || 0) / 100;
  const taxAmount = subtotal * taxRate;
  const total = subtotal + taxAmount;

  const updateItem = (id: number, patch: Partial<DraftItem>) =>
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, ...patch } : i)));

  const createMutation = useMutation({
    mutationFn: createSalesInvoice,
    onSuccess: (invoice) => {
      toast.success(`Invoice ${invoice.invoice_number ?? invoice.id.slice(0, 8)} saved`);
      setSaved(invoice);
      queryClient.invalidateQueries({ queryKey: ["invoices", "sales"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not save this invoice"),
  });

  const startNew = () => {
    setSaved(null);
    setForm(blankForm());
    setItems([{ id: Date.now(), desc: "", qty: 1, rate: 0 }]);
  };

  const submit = () => {
    if (!form.customerName.trim()) {
      toast.error("A customer name is required");
      return;
    }
    const cleanItems = items.filter((i) => i.desc.trim());
    if (cleanItems.length === 0) {
      toast.error("Add at least one line item");
      return;
    }
    const badQty = cleanItems.find((i) => !(i.qty > 0));
    if (badQty) {
      toast.error(`"${badQty.desc}" needs a quantity greater than 0`);
      return;
    }
    const badRate = cleanItems.find((i) => i.rate < 0);
    if (badRate) {
      toast.error(`"${badRate.desc}" has a negative rate`);
      return;
    }
    const payload: SalesInvoiceCreatePayload = {
      customer_name: form.customerName.trim(),
      invoice_number: form.invoiceNumber.trim() || null,
      invoice_date: form.invoiceDate || null,
      ntn: form.ntn.trim() || null,
      tax_rate: taxRate,
      items: cleanItems.map((i) => ({ description: i.desc.trim(), qty: i.qty, rate: i.rate })),
    };
    createMutation.mutate(payload);
  };

  const handleDownload = async (invoice: SalesInvoice) => {
    setDownloadingId(invoice.id);
    try {
      await downloadPdf(invoice.id, `invoice-${invoice.invoice_number ?? invoice.id.slice(0, 8)}.pdf`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not download this PDF");
    } finally {
      setDownloadingId(null);
    }
  };

  async function handleLogoChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets choosing the same file again re-fire onChange
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      toast.error("Please choose an image file");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      toast.error(`That logo is too large — keep it under ${Math.round(MAX_LOGO_BYTES / 1000)}KB`);
      return;
    }
    setUploadingLogo(true);
    try {
      const dataUrl = await readFileAsDataUrl(file);
      setLogoUrl(dataUrl);
      await brandingMutation.mutateAsync({ logo_url: dataUrl });
      toast.success("Logo updated");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not upload this logo");
    } finally {
      setUploadingLogo(false);
    }
  }

  function removeLogo() {
    setLogoUrl(null);
    brandingMutation.mutate({ logo_url: null });
  }

  function selectTemplate(key: InvoiceTemplateKey) {
    setTemplate(key);
    brandingMutation.mutate({ invoice_template: key });
  }

  function selectPlacement(value: LogoPlacement) {
    setLogoPlacement(value);
    brandingMutation.mutate({ logo_placement: value });
  }

  // Once saved, the preview shows the authoritative server record — its
  // totals are computed there, not trusted from the client's own math.
  const previewCustomer = saved?.customer_name ?? form.customerName;
  const previewNumber = saved?.invoice_number ?? form.invoiceNumber;
  const previewDate = saved?.invoice_date ?? form.invoiceDate;
  const previewNtn = saved?.ntn ?? form.ntn;
  const previewItems = saved
    ? saved.items.map((i) => ({ id: i.id, desc: i.description ?? "", qty: i.qty ?? 0, rate: i.rate ?? 0, amount: i.amount ?? 0 }))
    : items.filter((i) => i.desc.trim()).map((i) => ({ id: String(i.id), desc: i.desc, qty: i.qty, rate: i.rate, amount: i.qty * i.rate }));
  const previewSubtotal = saved?.subtotal ?? subtotal;
  const previewTaxAmount = saved?.tax_amount ?? taxAmount;
  const previewTaxRate = saved?.tax_rate ?? taxRate;
  const previewTotal = saved?.total ?? total;

  const style = templateStyle(template);
  const companyName = companyQuery.data?.name || "FinPilot AI";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold">Invoice Generator</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Build a sales invoice with live preview and automatic sales tax.
          </p>
        </div>
        <div className="flex gap-2">
          {saved ? (
            <>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={startNew}>
                <Plus className="h-4 w-4" /> New Invoice
              </Button>
              <Button
                className="gap-2 rounded-xl"
                onClick={() => handleDownload(saved)}
                disabled={downloadingId === saved.id}
              >
                {downloadingId === saved.id
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <FileDown className="h-4 w-4" />} Download PDF
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={() => window.print()}>
                <Printer className="h-4 w-4" /> Print preview
              </Button>
              <Button className="gap-2 rounded-xl" onClick={submit} disabled={createMutation.isPending}>
                {createMutation.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <FileDown className="h-4 w-4" />} Save Invoice
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="surface space-y-5 p-6 print:hidden">
          {saved && (
            <div className="rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
              Saved as <strong>{saved.invoice_number || saved.id.slice(0, 8)}</strong>. This invoice can still be
              edited from the Saved Records grid.
            </div>
          )}

          <div className="rounded-xl border p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">Branding</p>
                <p className="text-xs text-muted-foreground">Applies to every invoice — edit anytime.</p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <div
                className="grid h-14 w-14 shrink-0 place-items-center overflow-hidden rounded-xl border bg-muted/40"
              >
                {logoUrl ? (
                  <img src={logoUrl} alt="Company logo" className="h-full w-full object-contain p-1" />
                ) : (
                  <ImageUp className="h-5 w-5 text-muted-foreground" />
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <div className="flex gap-2">
                  <Button
                    size="sm" variant="outline" className="gap-1.5 rounded-lg"
                    disabled={uploadingLogo}
                    onClick={() => logoInputRef.current?.click()}
                  >
                    {uploadingLogo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImageUp className="h-3.5 w-3.5" />}
                    {logoUrl ? "Replace logo" : "Upload logo"}
                  </Button>
                  {logoUrl && (
                    <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground" onClick={removeLogo}>
                      <X className="h-3.5 w-3.5" /> Remove
                    </Button>
                  )}
                </div>
                <input
                  ref={logoInputRef} type="file" accept="image/*" className="hidden" onChange={handleLogoChange}
                />
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-muted-foreground">Placement</Label>
                  <Select value={logoPlacement} onValueChange={(v) => selectPlacement(v as LogoPlacement)}>
                    <SelectTrigger className="h-7 w-28 rounded-lg text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="left">Left</SelectItem>
                      <SelectItem value="center">Center</SelectItem>
                      <SelectItem value="right">Right</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="customer">Customer</Label>
            <Input
              id="customer"
              placeholder="Al-Madina Retail (Pvt) Ltd"
              value={form.customerName}
              onChange={(e) => setForm({ ...form, customerName: e.target.value })}
              disabled={!!saved}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="num">Invoice # (optional)</Label>
              <Input
                id="num"
                placeholder="INV-2026-0184"
                value={form.invoiceNumber}
                onChange={(e) => setForm({ ...form, invoiceNumber: e.target.value })}
                disabled={!!saved}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="date">Issue date</Label>
              <Input
                id="date"
                type="date"
                value={form.invoiceDate}
                onChange={(e) => setForm({ ...form, invoiceDate: e.target.value })}
                disabled={!!saved}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ntn">Customer NTN (optional)</Label>
              <Input
                id="ntn"
                placeholder="4820193-6"
                value={form.ntn}
                onChange={(e) => setForm({ ...form, ntn: e.target.value })}
                disabled={!!saved}
              />
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>Line items</Label>
              {!saved && (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5 rounded-lg"
                  onClick={() => setItems((p) => [...p, { id: Date.now(), desc: "", qty: 1, rate: 0 }])}
                >
                  <Plus className="h-3.5 w-3.5" /> Add item
                </Button>
              )}
            </div>
            {!saved && items.map((it) => (
              <div key={it.id} className="grid grid-cols-[minmax(0,1fr)_4.5rem_6.5rem_2rem] items-center gap-2">
                <Input
                  aria-label="Description"
                  placeholder="Description"
                  value={it.desc}
                  onChange={(e) => updateItem(it.id, { desc: e.target.value })}
                />
                <Input
                  aria-label="Quantity"
                  type="number"
                  min={0}
                  value={it.qty}
                  onChange={(e) => updateItem(it.id, { qty: Number(e.target.value) })}
                />
                <Input
                  aria-label="Rate"
                  type="number"
                  min={0}
                  value={it.rate}
                  onChange={(e) => updateItem(it.id, { rate: Number(e.target.value) })}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Remove item"
                  disabled={items.length === 1}
                  onClick={() => setItems((p) => p.filter((x) => x.id !== it.id))}
                >
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
            ))}
          </div>

          {!saved && (
            <div className="space-y-1.5 sm:max-w-[10rem]">
              <Label htmlFor="tax">Sales tax %</Label>
              <Input
                id="tax"
                type="number"
                min={0}
                max={100}
                value={form.taxRatePercent}
                onChange={(e) => setForm({ ...form, taxRatePercent: e.target.value })}
              />
            </div>
          )}
        </div>

        <div className="space-y-3">
          <div
            className="surface overflow-hidden p-6 sm:p-8"
            style={{ backgroundColor: style.pageBg, color: style.cardText }}
          >
            <div className="flex flex-col gap-4" style={{ alignItems: logoPlacement === "center" ? "center" : "stretch" }}>
              <div
                className="flex items-start gap-2.5"
                style={{ justifyContent: logoPlacement === "center" ? "center" : LOGO_JUSTIFY[logoPlacement] }}
              >
                {logoUrl ? (
                  <img src={logoUrl} alt={companyName} className="h-10 w-auto max-w-[9rem] object-contain" />
                ) : logoPlacement !== "center" ? (
                  <span
                    className="grid h-10 w-10 place-items-center rounded-xl text-white"
                    style={{ backgroundColor: style.accent }}
                  >
                    <Wallet className="h-5 w-5" />
                  </span>
                ) : null}
              </div>
              <div
                className="flex w-full flex-wrap items-start justify-between gap-4"
                style={{ flexDirection: logoPlacement === "center" ? "column" : "row", alignItems: logoPlacement === "center" ? "center" : "flex-start" }}
              >
                <div style={{ textAlign: logoPlacement === "center" ? "center" : "left" }}>
                  <p className="font-display text-base font-bold">{companyName}</p>
                  <p className="text-xs" style={{ color: style.mutedText }}>Karachi, Pakistan</p>
                </div>
                <div style={{ textAlign: logoPlacement === "center" ? "center" : "right" }}>
                  <p className="font-display text-xl font-bold" style={{ color: style.accent }}>SALES INVOICE</p>
                  {previewNumber && <p className="text-xs" style={{ color: style.mutedText }}>{previewNumber}</p>}
                </div>
              </div>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <div>
                <p className="text-[11px] uppercase tracking-wide" style={{ color: style.mutedText }}>Billed to</p>
                <p className="mt-1 whitespace-pre-line text-sm leading-relaxed">
                  {previewCustomer || "—"}
                  {previewNtn ? `\nNTN ${previewNtn}` : ""}
                </p>
              </div>
              <div className="sm:text-right">
                <p className="text-[11px] uppercase tracking-wide" style={{ color: style.mutedText }}>Issued</p>
                <p className="text-sm">{previewDate || "—"}</p>
              </div>
            </div>

            <div className="mt-6 overflow-x-auto rounded-xl border" style={{ borderColor: style.ruleColor }}>
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase tracking-wide" style={{ backgroundColor: style.accent, color: style.onAccent }}>
                  <tr>
                    <th className="px-3 py-2 font-medium">Description</th>
                    <th className="px-3 py-2 text-right font-medium">Qty</th>
                    <th className="px-3 py-2 text-right font-medium">Rate</th>
                    <th className="px-3 py-2 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {previewItems.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-3 py-4 text-center" style={{ color: style.mutedText }}>No items yet</td>
                    </tr>
                  ) : (
                    previewItems.map((it, i) => (
                      <tr
                        key={it.id}
                        style={{
                          borderTop: `1px solid ${style.ruleColor}`,
                          backgroundColor: style.zebraBg && i % 2 === 1 ? style.zebraBg : "transparent",
                        }}
                      >
                        <td className="px-3 py-2.5">{it.desc || "—"}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{it.qty}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{money(it.rate)}</td>
                        <td className="px-3 py-2.5 text-right font-medium tabular-nums">{money(it.amount)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="mt-5 ml-auto max-w-xs space-y-2 text-sm">
              <div className="flex justify-between">
                <span style={{ color: style.mutedText }}>Subtotal</span>
                <span className="tabular-nums">{money(previewSubtotal)}</span>
              </div>
              <div className="flex justify-between">
                <span style={{ color: style.mutedText }}>Sales tax ({(previewTaxRate * 100).toFixed(2)}%)</span>
                <span className="tabular-nums">{money(previewTaxAmount)}</span>
              </div>
              <div
                className="flex justify-between pt-2 font-display text-base font-bold"
                style={{ borderTop: `1px solid ${style.ruleColor}` }}
              >
                <span>Total due</span>
                <span className="tabular-nums" style={{ color: style.accent }}>{money(previewTotal)}</span>
              </div>
            </div>
          </div>

          <div className="surface p-4 print:hidden">
            <p className="mb-3 text-sm font-medium">Templates</p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {INVOICE_TEMPLATES.map((t) => (
                <InvoiceTemplateThumbnail
                  key={t.key}
                  template={t}
                  active={t.key === template}
                  logoUrl={logoUrl}
                  logoPlacement={logoPlacement}
                  onClick={() => selectTemplate(t.key)}
                />
              ))}
            </div>
          </div>
        </div>
      </div>

      <section className="surface overflow-hidden">
        <header className="border-b px-5 py-4">
          <h3 className="text-base font-semibold">Sales Invoices</h3>
          <p className="text-xs text-muted-foreground">Every invoice generated so far</p>
        </header>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice #</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {listQuery.isPending ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <TableRow key={i}><TableCell colSpan={5}><Skeleton className="h-6 w-full" /></TableCell></TableRow>
                ))
              ) : listQuery.isError ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-destructive">
                    {(listQuery.error as Error).message || "Could not load sales invoices"}
                  </TableCell>
                </TableRow>
              ) : (listQuery.data?.invoices.length ?? 0) === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                    No sales invoices yet — the one you save above will appear here.
                  </TableCell>
                </TableRow>
              ) : (
                listQuery.data!.invoices.map((inv) => (
                  <TableRow key={inv.id} className="hover:bg-muted/50">
                    <TableCell className="font-medium">{inv.invoice_number ?? "—"}</TableCell>
                    <TableCell>{inv.customer_name ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{inv.invoice_date ?? "—"}</TableCell>
                    <TableCell className="text-right font-medium">{moneyOrDash(inv.total)}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="gap-1.5"
                        disabled={downloadingId === inv.id}
                        onClick={() => handleDownload(inv)}
                      >
                        {downloadingId === inv.id
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Download className="h-3.5 w-3.5" />} PDF
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  );
}
