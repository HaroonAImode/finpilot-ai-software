import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Bot, Send, Sparkles, TrendingUp, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/app-shell";
import { insights } from "@/lib/data";

export const Route = createFileRoute("/app/assistant")({
  head: () => ({
    meta: [
      { title: "AI Assistant — FinPilot AI" },
      { name: "description", content: "Ask questions about your business finances and get instant AI answers." },
      { property: "og:title", content: "AI Assistant — FinPilot AI" },
      { property: "og:description", content: "Conversational AI insights on your accounting data." },
    ],
  }),
  component: AssistantPage,
});

type Msg = { role: "user" | "ai"; text: string };

const suggestions = [
  "Why did expenses increase this month?",
  "Which vendor costs us the most?",
  "Show unpaid invoices.",
  "What is my estimated profit?",
];

const canned: Record<string, string> = {
  "Why did expenses increase this month?":
    "July expenses rose 3.6% to PKR 6.12M. The main drivers were fuel & transport (+22%, PKR 780K) after three extra Karachi–Lahore runs, and utilities (+11%) due to peak summer load. Salaries stayed flat at PKR 2.18M.",
  "Which vendor costs us the most?":
    "ABC Traders — PKR 1.24M across 28 invoices this quarter (30% of total vendor spend). At this volume you qualify for a 4% bulk discount, worth roughly PKR 49,600 per quarter.",
  "Show unpaid invoices.":
    "You have 34 pending invoices worth PKR 2.86M. The three largest: Faisalabad Textiles PKR 312,000 (due 09 Aug), Karachi Steel Co. PKR 96,200 (due 12 Aug) and Lahore Packaging PKR 42,800 (in review).",
  "What is my estimated profit?":
    "Net profit for July is PKR 3.72M at a 37.8% margin — up 22.8% month over month. Based on current momentum, August is projected at PKR 4.05M ± PKR 260K.",
};

function AssistantPage() {
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "ai",
      text: "Hello Ayesha — I've analysed 1,345 documents from your July books. Ask me anything about revenue, expenses, vendors or cash flow.",
    },
  ]);
  const [input, setInput] = useState("");

  const send = (text: string) => {
    if (!text.trim()) return;
    const reply =
      canned[text] ??
      "Based on your July ledger, that trend looks stable. Revenue is PKR 9.84M against PKR 6.12M of expenses, leaving PKR 3.72M net profit and 4.2 months of cash runway.";
    setMessages((m) => [...m, { role: "user", text }, { role: "ai", text: reply }]);
    setInput("");
  };

  return (
    <>
      <PageHeader
        title="Ask AI about your business"
        subtitle="Your accounting copilot — trained on your ledgers, invoices and payroll."
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className="surface flex min-h-[540px] flex-col overflow-hidden xl:col-span-2">
          <header className="flex items-center gap-3 border-b px-5 py-4">
            <span className="gradient-brand grid h-9 w-9 shrink-0 place-items-center rounded-xl text-primary-foreground">
              <Sparkles className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">FinPilot Copilot</p>
              <p className="text-xs text-success">● Online · analysing live data</p>
            </div>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {messages.map((m, i) => (
              <div key={i} className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                <span
                  className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${
                    m.role === "ai" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
                  }`}
                >
                  {m.role === "ai" ? <Bot className="h-4 w-4" /> : <User className="h-4 w-4" />}
                </span>
                <p
                  className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                    m.role === "ai"
                      ? "rounded-tl-sm bg-muted/70"
                      : "rounded-tr-sm bg-[image:var(--gradient-brand)] text-primary-foreground"
                  }`}
                >
                  {m.text}
                </p>
              </div>
            ))}
          </div>

          <div className="border-t p-4">
            <div className="mb-3 flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                >
                  {s}
                </button>
              ))}
            </div>
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about revenue, vendors, taxes…"
                className="rounded-xl"
              />
              <Button type="submit" size="icon" className="shrink-0 rounded-xl">
                <Send className="h-4 w-4" />
              </Button>
            </form>
          </div>
        </section>

        <div className="flex flex-col gap-4">
          <h3 className="font-display text-lg font-semibold">AI Insight Cards</h3>
          {insights.map((ins) => (
            <div key={ins.title} className="surface lift p-5">
              <span
                className={`grid h-9 w-9 place-items-center rounded-xl ${
                  ins.tone === "positive"
                    ? "bg-success/12 text-success"
                    : ins.tone === "warning"
                      ? "bg-warning/15 text-warning"
                      : "bg-primary/10 text-primary"
                }`}
              >
                <TrendingUp className="h-4 w-4" />
              </span>
              <p className="mt-3 text-sm font-semibold">{ins.title}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{ins.body}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
