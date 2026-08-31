/**
 * Transactions Service client — the expense ledger and Dashboard KPIs
 * (backend/services/transactions-service, port 8003, routed through the
 * Gateway at /api/v1/expenses and /api/v1/transactions).
 *
 * Revenue is deliberately not part of this client: it stays Invoice
 * Service's own scanned-sales ledger (see invoice-service.ts's
 * getSalesSummary) rather than a second table here — see
 * docs/superpowers/specs/2026-08-29-transactions-service-design.md.
 *
 * Same access-token-getter and `request()` shape as the other clients,
 * registered the same way in auth-context.tsx.
 */

export type ExpenseStatus = "pending" | "approved" | "rejected";
export type ExpenseSource = "manual" | "invoice";
export type PaymentMethod = "bank_transfer" | "online" | "cheque" | "cash" | "card";

export interface Expense {
  id: string;
  category: string;
  vendor_name: string | null;
  vendor_id: string | null;
  /** Set only when this expense was auto-booked from a purchase invoice's
   *  "Send to Accounting" (source === "invoice") — see the design spec. */
  invoice_id: string | null;
  date: string;
  amount_pkr: number;
  payment_method: PaymentMethod | null;
  status: ExpenseStatus;
  source: ExpenseSource;
  reference_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExpenseListResponse {
  expenses: Expense[];
  total: number;
  skip: number;
  limit: number;
}

export interface ExpenseOptions {
  categories: string[];
  payment_methods: PaymentMethod[];
}

export interface CategoryBreakdownPoint {
  name: string;
  value: number;
}

export interface ExpenseSummary {
  total: number;
  approved: number;
  pending: number;
  rejected: number;
  pending_count: number;
  rejected_count: number;
}

export interface ExpenseCreatePayload {
  category: string;
  vendor_name?: string | null;
  date: string;
  amount_pkr: number;
  payment_method?: PaymentMethod | null;
  reference_id?: string | null;
}

export type ExpenseUpdatePayload = Partial<ExpenseCreatePayload>;

export interface MonthlyPoint {
  month: string;
  revenue: number;
  expenses: number;
}

export interface CashFlowPoint {
  month: string;
  inflow: number;
  outflow: number;
}

export interface KpiSet {
  today_revenue: number;
  monthly_revenue: number;
  monthly_expenses: number;
  net_profit: number;
  /** Cumulative all-time revenue minus all-time approved expenses — a
   *  derived approximation, not a real bank balance (no cash/bank account
   *  model exists yet). */
  cash_balance: number;
  employee_salaries: number;
  pending_invoices: number;
  processed_invoices: number;
  /** True when Invoice Service could not be reached for the invoice-
   *  derived figures above — the UI should show these as "unavailable"
   *  rather than a confident zero, same convention as vendors-service's
   *  own spend_unavailable. */
  revenue_unavailable: boolean;
}

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const token = accessTokenGetter();
  const response = await fetch(`${base}${path}`, {
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
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const EXPENSES = "/api/v1/expenses";
const TRANSACTIONS = "/api/v1/transactions";

export function listExpenses(
  params: {
    category?: string; status?: ExpenseStatus; date_from?: string; date_to?: string;
    search?: string; skip?: number; limit?: number;
  } = {},
): Promise<ExpenseListResponse> {
  const query = new URLSearchParams();
  if (params.category) query.set("category", params.category);
  if (params.status) query.set("status", params.status);
  if (params.date_from) query.set("date_from", params.date_from);
  if (params.date_to) query.set("date_to", params.date_to);
  if (params.search) query.set("search", params.search);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 50));
  return request<ExpenseListResponse>(EXPENSES, `/?${query.toString()}`);
}

export function expenseOptions(): Promise<ExpenseOptions> {
  return request<ExpenseOptions>(EXPENSES, "/options");
}

export function expenseCategoryBreakdown(): Promise<CategoryBreakdownPoint[]> {
  return request<CategoryBreakdownPoint[]>(EXPENSES, "/categories");
}

export function expenseSummary(): Promise<ExpenseSummary> {
  return request<ExpenseSummary>(EXPENSES, "/summary");
}

export function createExpense(payload: ExpenseCreatePayload): Promise<Expense> {
  return request<Expense>(EXPENSES, "/", { method: "POST", body: JSON.stringify(payload) });
}

export function updateExpense(expenseId: string, payload: ExpenseUpdatePayload): Promise<Expense> {
  return request<Expense>(EXPENSES, `/${expenseId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function approveExpense(expenseId: string): Promise<Expense> {
  return request<Expense>(EXPENSES, `/${expenseId}/approve`, { method: "PUT" });
}

export function rejectExpense(expenseId: string): Promise<Expense> {
  return request<Expense>(EXPENSES, `/${expenseId}/reject`, { method: "PUT" });
}

export function transactionsKpis(): Promise<KpiSet> {
  return request<KpiSet>(TRANSACTIONS, "/kpis");
}

export function revenueVsExpensesChart(): Promise<MonthlyPoint[]> {
  return request<MonthlyPoint[]>(TRANSACTIONS, "/chart/revenue-vs-expenses");
}

export function cashFlowChart(): Promise<CashFlowPoint[]> {
  return request<CashFlowPoint[]>(TRANSACTIONS, "/chart/cash-flow");
}
