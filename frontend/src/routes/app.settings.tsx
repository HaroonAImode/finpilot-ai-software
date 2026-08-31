import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/app-shell";
import { useTheme } from "@/components/theme-toggle";
import { useAuth } from "@/lib/auth-context";
import { ConnectedAppsCard } from "@/components/slack/connected-apps-card";
import { SlackFilesSheet } from "@/components/slack/slack-files-sheet";
import { EmailConnectedAppsCard } from "@/components/email/connected-apps-card";
import { EmailFilesSheet } from "@/components/email/email-files-sheet";
import { NeedsReviewTray } from "@/components/email/needs-review-tray";
import { SenderRulesCard } from "@/components/email/sender-rules-card";
import {
  getAutomationSettings, getCompanyProfile, getTaxSettings, updateAutomationSettings,
  updateCompanyProfile, updateTaxSettings,
  type AutomationSettings, type CompanyProfile as CompanyProfileData, type FilerStatus, type TaxSettings,
} from "@/lib/settings-service";

export const Route = createFileRoute("/app/settings")({
  head: () => ({
    meta: [
      { title: "Settings — FinPilot AI" },
      { name: "description", content: "Company profile, tax preferences, AI automation and appearance settings." },
      { property: "og:title", content: "Settings — FinPilot AI" },
      { property: "og:description", content: "Manage company, tax and AI automation preferences." },
    ],
  }),
  component: SettingsPage,
});

function Row({
  title, desc, checked, onCheckedChange, disabled,
}: {
  title: string; desc: string; checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-4">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{desc}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
    </div>
  );
}

/** Decorative-only row for a preference nothing on the backend persists —
 *  Appearance's "Compact tables"/"Animated charts" are pure client UI
 *  taste with no Settings Service field (architecture report §5.10 names
 *  Company/Automation/Tax only), left exactly as they were rather than
 *  inventing a backend field for them. */
function DecorativeRow({ title, desc, defaultChecked = false }: { title: string; desc: string; defaultChecked?: boolean }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-4">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{desc}</p>
      </div>
      <Switch defaultChecked={defaultChecked} />
    </div>
  );
}

type CompanyForm = Omit<CompanyProfileData, "created_at" | "updated_at">;

function toCompanyForm(data: CompanyProfileData): CompanyForm {
  const { created_at: _c, updated_at: _u, ...rest } = data;
  return rest;
}

/**
 * Company profile.
 *
 * Identity fields (Business Name/Your Name/Email/Role) come from the
 * signed-in account (Auth Service) and stay read-only — Auth deliberately
 * exposes no edit endpoint for them. Everything below that is the
 * extended profile Settings Service now owns: NTN, address, industry,
 * contact details, and tax configuration. Its own `name` field is
 * deliberately **not** pre-filled from the Auth-issued company name —
 * see settings-service.ts's CompanyProfile docstring for why the two
 * are not synchronised.
 */
function CompanyProfile() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const identity: [string, string][] = [
    ["Business Name", user?.company_name ?? "—"],
    ["Your Name", user?.full_name ?? "—"],
    ["Email", user?.email ?? "—"],
    ["Role", user?.role ?? "—"],
  ];

  const profileQuery = useQuery({ queryKey: ["settings-company"], queryFn: getCompanyProfile });
  const taxQuery = useQuery({ queryKey: ["settings-tax"], queryFn: getTaxSettings });

  const [companyForm, setCompanyForm] = useState<CompanyForm | null>(null);
  const [taxForm, setTaxForm] = useState<TaxSettings | null>(null);

  useEffect(() => {
    if (profileQuery.data) setCompanyForm(toCompanyForm(profileQuery.data));
  }, [profileQuery.data]);
  useEffect(() => {
    if (taxQuery.data) setTaxForm(taxQuery.data);
  }, [taxQuery.data]);

  const saveCompanyMutation = useMutation({
    mutationFn: updateCompanyProfile,
    onSuccess: () => {
      toast.success("Company profile saved");
      queryClient.invalidateQueries({ queryKey: ["settings-company"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const saveTaxMutation = useMutation({
    mutationFn: updateTaxSettings,
    onSuccess: () => {
      toast.success("Tax configuration saved");
      queryClient.invalidateQueries({ queryKey: ["settings-tax"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <div className="space-y-4">
      <section className="surface max-w-3xl p-6">
        <h3 className="text-base font-semibold">Company Profile</h3>
        <p className="mt-1 text-sm text-muted-foreground">From your FinPilot account.</p>

        <dl className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {identity.map(([label, value]) => (
            <div key={label} className="space-y-1">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="truncate text-sm font-medium capitalize" title={value}>
                {value}
              </dd>
            </div>
          ))}
        </dl>

        <Separator className="my-6" />

        <h4 className="text-sm font-semibold">Business Details</h4>
        <p className="mt-1 text-xs text-muted-foreground">
          Not synchronised with the Business Name above — this is your own record, separate from your account.
        </p>

        {!companyForm ? (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
          </div>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Legal / Display Name</Label>
              <Input
                value={companyForm.name ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, name: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>NTN</Label>
              <Input
                value={companyForm.ntn ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, ntn: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Address</Label>
              <Input
                value={companyForm.address ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, address: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>City</Label>
              <Input
                value={companyForm.city ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, city: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Industry</Label>
              <Input
                value={companyForm.industry ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, industry: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Phone</Label>
              <Input
                value={companyForm.phone ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, phone: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Business Contact Email</Label>
              <Input
                type="email" value={companyForm.email ?? ""} onChange={(e) => setCompanyForm({ ...companyForm, email: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <Button
            disabled={!companyForm || saveCompanyMutation.isPending}
            onClick={() => companyForm && saveCompanyMutation.mutate(companyForm)}
          >
            {saveCompanyMutation.isPending ? "Saving…" : "Save Business Details"}
          </Button>
        </div>
      </section>

      <section className="surface max-w-3xl p-6">
        <h3 className="text-base font-semibold">Tax Configuration</h3>
        <p className="mt-1 text-xs text-muted-foreground">Defaults to 18% GST, the standard Pakistani rate.</p>

        {!taxForm ? (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
          </div>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Default GST Rate (%)</Label>
              <Input
                type="number" value={taxForm.default_gst_rate}
                onChange={(e) => setTaxForm({ ...taxForm, default_gst_rate: Number(e.target.value) })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Withholding Tax Rate (%)</Label>
              <Input
                type="number" value={taxForm.withholding_tax_rate}
                onChange={(e) => setTaxForm({ ...taxForm, withholding_tax_rate: Number(e.target.value) })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Filer Status</Label>
              <Select value={taxForm.filer_status} onValueChange={(v) => setTaxForm({ ...taxForm, filer_status: v as FilerStatus })}>
                <SelectTrigger className="rounded-xl"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="filer">Filer</SelectItem>
                  <SelectItem value="non_filer">Non-Filer</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <Button
            disabled={!taxForm || saveTaxMutation.isPending}
            onClick={() => taxForm && saveTaxMutation.mutate(taxForm)}
          >
            {saveTaxMutation.isPending ? "Saving…" : "Save Tax Configuration"}
          </Button>
        </div>
      </section>
    </div>
  );
}

/** AI automation toggles — architecture report §5.10's exact five flags.
 *  Toggling saves immediately (no separate Save button), the usual
 *  pattern for a settings switch. Real, persisted preferences — but see
 *  each row's own description for which are actually enforced by the
 *  pipeline today versus reserved for a later phase; none of the five is
 *  currently silently pretending to do more than it does. */
function AutomationTab() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["settings-automation"], queryFn: getAutomationSettings });

  const mutation = useMutation({
    mutationFn: updateAutomationSettings,
    onSuccess: (data) => queryClient.setQueryData(["settings-automation"], data),
    onError: (error: Error) => toast.error(error.message),
  });

  function toggle(field: keyof Omit<AutomationSettings, "created_at" | "updated_at">, value: boolean) {
    mutation.mutate({ [field]: value });
  }

  if (!query.data) {
    return (
      <section className="surface max-w-3xl divide-y p-6">
        {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="my-2 h-12 rounded-xl" />)}
      </section>
    );
  }

  const s = query.data;
  return (
    <section className="surface max-w-3xl divide-y p-6">
      <Row
        title="Auto-categorize expenses"
        desc="Suggest a category automatically when an expense is booked from a scanned invoice. Reserved — not yet wired into the extraction pipeline."
        checked={s.auto_categorize_expenses} onCheckedChange={(v) => toggle("auto_categorize_expenses", v)}
      />
      <Row
        title="Duplicate detection"
        desc="Flag a re-uploaded file as an existing invoice. The Scanner's own dedup check runs unconditionally today; this will make it optional."
        checked={s.auto_detect_duplicates} onCheckedChange={(v) => toggle("auto_detect_duplicates", v)}
      />
      <Row
        title="AI Insights"
        desc="Generate insight cards on your dashboard. Not built yet — this toggle has nothing to control yet."
        checked={s.auto_insights} onCheckedChange={(v) => toggle("auto_insights", v)}
      />
      <Row
        title="Smart vendor suggestions"
        desc="Suggest a vendor match when reconciling an OCR'd name. Vendor Reconciliation's fuzzy matching runs unconditionally today; this will make it optional."
        checked={s.smart_vendor_suggestions} onCheckedChange={(v) => toggle("smart_vendor_suggestions", v)}
      />
      <Row
        title="Enable AI features"
        desc="Master switch for AI-powered features across FinPilot. Not yet enforced — every AI feature runs regardless of this toggle today."
        checked={s.enabled_ai} onCheckedChange={(v) => toggle("enabled_ai", v)}
      />
    </section>
  );
}

function SettingsPage() {
  const { dark, toggle } = useTheme();
  const [filesSheetOpen, setFilesSheetOpen] = useState(false);
  const [emailFilesSheetOpen, setEmailFilesSheetOpen] = useState(false);

  return (
    <>
      <PageHeader title="Settings" subtitle="Configure your workspace, taxes and AI automation." />

      <Tabs defaultValue="company" className="w-full">
        <TabsList className="rounded-xl">
          <TabsTrigger value="company">Company</TabsTrigger>
          <TabsTrigger value="automation">AI Automation</TabsTrigger>
          <TabsTrigger value="appearance">Appearance</TabsTrigger>
          <TabsTrigger value="connected-apps">Connected Apps</TabsTrigger>
        </TabsList>

        <TabsContent value="company" className="mt-4">
          <CompanyProfile />
        </TabsContent>

        <TabsContent value="automation" className="mt-4">
          <AutomationTab />
        </TabsContent>

        <TabsContent value="appearance" className="mt-4">
          <section className="surface max-w-3xl p-6">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Dark mode</p>
                <p className="text-xs text-muted-foreground">Easier on the eyes during late-night closings.</p>
              </div>
              <Switch checked={dark} onCheckedChange={toggle} />
            </div>
            <Separator className="my-6" />
            <DecorativeRow title="Compact tables" desc="Show more rows per screen." />
            <DecorativeRow title="Animated charts" desc="Enable motion on dashboard visualisations." defaultChecked />
          </section>
        </TabsContent>

        <TabsContent value="connected-apps" className="mt-4 space-y-4">
          <ConnectedAppsCard onBrowseFiles={() => setFilesSheetOpen(true)} />
          <SlackFilesSheet open={filesSheetOpen} onOpenChange={setFilesSheetOpen} />
          <EmailConnectedAppsCard onBrowseFiles={() => setEmailFilesSheetOpen(true)} />
          <EmailFilesSheet open={emailFilesSheetOpen} onOpenChange={setEmailFilesSheetOpen} />
          <NeedsReviewTray />
          <SenderRulesCard />
        </TabsContent>
      </Tabs>
    </>
  );
}
