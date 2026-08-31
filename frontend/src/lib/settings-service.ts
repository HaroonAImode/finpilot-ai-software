/**
 * Settings Service client — company profile, tax configuration, and AI
 * automation toggles (backend/services/settings-service, port 8009,
 * routed through the Gateway at /api/v1/settings).
 *
 * Same access-token-getter and `request()` shape as the other clients,
 * registered the same way in auth-context.tsx.
 */

export type LogoPlacement = "left" | "center" | "right";
export type InvoiceTemplateKey = "classic" | "modern" | "midnight" | "minimal";

export interface CompanyProfile {
  name: string | null;
  ntn: string | null;
  address: string | null;
  city: string | null;
  /** A data: URI, not a hosted file URL — this service has no object
   *  storage of its own; see the Invoice Generator design notes. */
  logo_url: string | null;
  logo_placement: LogoPlacement;
  invoice_template: InvoiceTemplateKey;
  industry: string | null;
  phone: string | null;
  email: string | null;
  created_at: string;
  updated_at: string;
}

export type CompanyProfileUpdatePayload = Partial<Omit<CompanyProfile, "created_at" | "updated_at">>;

export interface AutomationSettings {
  auto_categorize_expenses: boolean;
  auto_detect_duplicates: boolean;
  auto_insights: boolean;
  smart_vendor_suggestions: boolean;
  enabled_ai: boolean;
  created_at: string;
  updated_at: string;
}

export type AutomationSettingsUpdatePayload = Partial<
  Omit<AutomationSettings, "created_at" | "updated_at">
>;

export type FilerStatus = "filer" | "non_filer";

export interface TaxSettings {
  default_gst_rate: number;
  withholding_tax_rate: number;
  filer_status: FilerStatus;
  created_at: string;
  updated_at: string;
}

export type TaxSettingsUpdatePayload = Partial<Omit<TaxSettings, "created_at" | "updated_at">>;

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

const BASE = "/api/v1/settings";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = accessTokenGetter();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request to ${path} failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getCompanyProfile(): Promise<CompanyProfile> {
  return request<CompanyProfile>("/company");
}

export function updateCompanyProfile(payload: CompanyProfileUpdatePayload): Promise<CompanyProfile> {
  return request<CompanyProfile>("/company", { method: "PUT", body: JSON.stringify(payload) });
}

export function getAutomationSettings(): Promise<AutomationSettings> {
  return request<AutomationSettings>("/automation");
}

export function updateAutomationSettings(payload: AutomationSettingsUpdatePayload): Promise<AutomationSettings> {
  return request<AutomationSettings>("/automation", { method: "PUT", body: JSON.stringify(payload) });
}

export function getTaxSettings(): Promise<TaxSettings> {
  return request<TaxSettings>("/tax");
}

export function updateTaxSettings(payload: TaxSettingsUpdatePayload): Promise<TaxSettings> {
  return request<TaxSettings>("/tax", { method: "PUT", body: JSON.stringify(payload) });
}
