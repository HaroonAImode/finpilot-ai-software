import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowRight,
  BarChart3,
  Bot,
  Boxes,
  Check,
  FilePlus2,
  ScanLine,
  Sparkles,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "FinPilot AI — AI-Powered Accounting Automation for Pakistani SMEs" },
      {
        name: "description",
        content:
          "Extract invoices from WhatsApp images, PDFs and scans, automate bookkeeping and get AI insights into your business finances.",
      },
      { property: "og:title", content: "FinPilot AI — AI-Powered Accounting Automation" },
      {
        property: "og:description",
        content: "Automate bookkeeping, extract invoices and analyze finances with AI built for Pakistani SMEs.",
      },
    ],
  }),
  component: Landing,
});

const stats = [
  { value: "1.3M+", label: "Invoices processed" },
  { value: "96%", label: "Extraction accuracy" },
  { value: "18 hrs", label: "Saved per month" },
  { value: "2,400+", label: "Pakistani SMEs" },
];

function Landing() {
  const features = [
    {
      icon: ScanLine,
      title: "AI Invoice Extraction",
      body: "Drop a PDF, photo or WhatsApp screenshot. Vendor, items, tax and totals are read in seconds with 96% accuracy.",
    },
    {
      icon: FilePlus2,
      title: "Instant Invoice Generator",
      body: "Build branded PKR invoices with live preview, automatic sales tax, discounts and one-click print or export.",
    },
    {
      icon: TrendingUp,
      title: "Revenue Automation",
      body: "Sales invoices post themselves — customers, products, payment method and tax captured automatically.",
    },
    {
      icon: BarChart3,
      title: "Financial Analytics",
      body: "Live dashboards for revenue, expenses, margin and cash flow, with forecasts you can actually trust.",
    },
    {
      icon: Boxes,
      title: "Procurement Management",
      body: "Purchase requests, approvals, orders and vendor comparison in a single tracked workflow.",
    },
    {
      icon: Bot,
      title: "AI Business Insights",
      body: "Ask why expenses rose, which vendor costs most, or what profit looks like next month — in plain language.",
    },
    {
      icon: Wallet,
      title: "Payroll & Expenses",
      body: "Salaries, bonuses, deductions and every rupee of spend, categorised and approval-ready.",
    },
  ];

  return (
    <div className="min-h-screen bg-background">
      <header className="glass sticky top-0 z-30 border-b">
        <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-4 py-3.5 sm:px-6">
          <Link to="/" className="flex min-w-0 items-center gap-2.5">
            <span className="gradient-brand grid h-9 w-9 shrink-0 place-items-center rounded-xl text-primary-foreground">
              <Wallet className="h-4.5 w-4.5" />
            </span>
            <span className="truncate font-display text-base font-bold">FinPilot AI</span>
          </Link>
          <div className="flex shrink-0 items-center gap-2">
            <ThemeToggle />
            <Button asChild variant="ghost" className="hidden rounded-xl sm:inline-flex">
              <Link to="/app">Live Demo</Link>
            </Button>
            <Button asChild className="gap-2 rounded-xl">
              <Link to="/app">
                Get Started <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>
      </header>

      <section className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute inset-x-0 -top-40 h-[520px] opacity-70"
          style={{ background: "var(--gradient-soft)" }}
        />
        <div className="relative mx-auto max-w-6xl px-4 py-20 text-center sm:px-6 sm:py-28">
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-medium text-muted-foreground shadow-[var(--shadow-soft)]">
            <Sparkles className="h-3.5 w-3.5 text-primary" /> Built for Pakistani SMEs
          </span>
          <h1 className="mx-auto mt-6 max-w-3xl font-display text-4xl font-extrabold leading-[1.08] sm:text-6xl">
            AI-Powered <span className="text-gradient">Accounting Automation</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground sm:text-lg">
            Automatically extract invoices from WhatsApp images, PDFs and scanned documents, organize your finances,
            and gain AI-powered insights.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Button asChild size="lg" className="gap-2 rounded-xl px-7">
              <Link to="/app">
                Get Started <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="rounded-xl px-7">
              <Link to="/app">Live Demo</Link>
            </Button>
          </div>

          <div className="surface mx-auto mt-16 max-w-4xl overflow-hidden p-3 text-left">
            <div className="rounded-xl border bg-muted/30 p-5">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  ["Monthly Revenue", "PKR 9.84M", "+18.2%"],
                  ["Monthly Expenses", "PKR 6.12M", "-5.1%"],
                  ["Net Profit", "PKR 3.72M", "+22.8%"],
                  ["Cash Balance", "PKR 14.26M", "+6.7%"],
                ].map(([label, value, delta]) => delta && (
                  <div key={label} className="rounded-xl border bg-card p-4">
                    <p className="truncate text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
                    <p className="mt-1 font-display text-lg font-bold">{value}</p>
                    <p
                      className={`text-[11px] font-semibold ${
                        delta.startsWith("-") ? "text-destructive" : "text-success"
                      }`}
                    >
                      {delta}
                    </p>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex h-40 items-end gap-2 rounded-xl border bg-card p-4">
                {[42, 55, 48, 63, 58, 74, 69, 88, 82, 96, 90, 100].map((h, i) => (
                  <span
                    key={i}
                    className="flex-1 rounded-t-md bg-[image:var(--gradient-brand)]"
                    style={{ height: `${h}%`, opacity: 0.45 + i * 0.045 }}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-y bg-card/50">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-4 py-12 sm:grid-cols-4 sm:px-6">
          {stats.map((s) => (
            <div key={s.label} className="text-center">
              <p className="font-display text-3xl font-bold text-gradient">{s.value}</p>
              <p className="mt-1 text-xs text-muted-foreground">{s.label}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <div className="max-w-2xl">
          <h2 className="font-display text-3xl font-bold sm:text-4xl">Everything your finance team does — automated</h2>
          <p className="mt-3 text-muted-foreground">
            One platform for bookkeeping, procurement, payroll and analysis. No spreadsheets, no data entry.
          </p>
        </div>
        <div className="mt-10 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <article key={f.title} className="surface lift p-6">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-[image:var(--gradient-soft)] text-primary">
                <f.icon className="h-5 w-5" />
              </span>
              <h3 className="mt-4 font-display text-lg font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
        <div className="surface grid grid-cols-1 items-center gap-8 overflow-hidden p-8 sm:p-12 lg:grid-cols-2">
          <div>
            <h2 className="font-display text-3xl font-bold">Close your books in hours, not weeks</h2>
            <ul className="mt-6 space-y-3">
              {[
                "Urdu & English invoice reading",
                "FBR-ready sales tax summaries",
                "Duplicate & fraud detection",
                "Bank-grade encryption on every document",
              ].map((t) => (
                <li key={t} className="flex items-start gap-3 text-sm">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-success/15 text-success">
                    <Check className="h-3 w-3" />
                  </span>
                  {t}
                </li>
              ))}
            </ul>
            <Button asChild className="mt-8 gap-2 rounded-xl">
              <Link to="/app">
                Explore the dashboard <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
          <div className="rounded-2xl border bg-[image:var(--gradient-soft)] p-6">
            <p className="text-sm leading-relaxed">
              "FinPilot replaced three people doing manual data entry. Our monthly close went from 11 days to a single
              afternoon, and I finally trust the numbers I show my bank."
            </p>
            <div className="mt-5 flex items-center gap-3">
              <span className="gradient-brand grid h-10 w-10 place-items-center rounded-full text-xs font-semibold text-primary-foreground">
                SR
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">Saad Rafiq</p>
                <p className="truncate text-xs text-muted-foreground">CFO, Al-Madina Retail · Karachi</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t bg-card/50">
        <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:grid-cols-2 sm:px-6 lg:grid-cols-4">
          <div>
            <div className="flex items-center gap-2.5">
              <span className="gradient-brand grid h-9 w-9 place-items-center rounded-xl text-primary-foreground">
                <Wallet className="h-4.5 w-4.5" />
              </span>
              <span className="font-display text-base font-bold">FinPilot AI</span>
            </div>
            <p className="mt-3 max-w-xs text-sm text-muted-foreground">
              Automate bookkeeping. Extract invoices. Analyze finances.
            </p>
          </div>
          {[
            { title: "Product", links: ["Invoice Scanner", "Revenue Manager", "Analytics", "AI Assistant"] },
            { title: "Company", links: ["About", "Careers", "Customers", "Contact"] },
            { title: "Resources", links: ["Documentation", "FBR Tax Guide", "Security", "Status"] },
          ].map((col) => (
            <div key={col.title}>
              <p className="text-sm font-semibold">{col.title}</p>
              <ul className="mt-3 space-y-2">
                {col.links.map((l) => (
                  <li key={l}>
                    <span className="cursor-pointer text-sm text-muted-foreground transition-colors hover:text-foreground">
                      {l}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="border-t px-4 py-5 text-center text-xs text-muted-foreground sm:px-6">
          © 2026 FinPilot AI. Built in Karachi, Pakistan.
        </div>
      </footer>
    </div>
  );
}
