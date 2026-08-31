/**
 * HR Service client — the employee directory and payroll processing
 * (backend/services/hr-service, port 8004, routed through the Gateway at
 * /api/v1/employees).
 *
 * Same access-token-getter and `request()` shape as the other clients,
 * registered the same way in auth-context.tsx.
 */

export interface Employee {
  id: string;
  name: string;
  department: string;
  /** Job title, e.g. "Backend Developer" — null only for employees created
   *  before this field existed. */
  role: string | null;
  salary_pkr: number;
  bonus_pkr: number;
  deductions_pkr: number;
  /** salary_pkr + bonus_pkr - deductions_pkr, as this record reads right
   *  now — not necessarily what a specific past month actually paid. */
  net_salary_pkr: number;
  joining_date: string;
  active: boolean;
  /** "paid" if this employee has a payroll record for the current
   *  calendar month, "pending" otherwise. */
  payment_status: "paid" | "pending";
  created_at: string;
  updated_at: string;
}

export interface EmployeeListResponse {
  employees: Employee[];
  total: number;
  skip: number;
  limit: number;
}

export interface EmployeeOptions {
  departments: string[];
  roles: string[];
}

export interface EmployeeCreatePayload {
  name: string;
  department: string;
  role: string;
  salary_pkr: number;
  bonus_pkr?: number;
  deductions_pkr?: number;
  joining_date: string;
  active?: boolean;
}

export type EmployeeUpdatePayload = Partial<EmployeeCreatePayload>;

export interface PayrollSummary {
  period: string;
  total_salary: number;
  total_bonus: number;
  total_deductions: number;
  total_net: number;
  employee_count: number;
  paid_count: number;
  pending_count: number;
}

export interface PayrollProcessResult {
  period: string;
  processed_count: number;
  already_processed_count: number;
  total_net: number;
  expense_booked: boolean;
}

export interface PayrollHistoryEntry {
  period: string;
  total_net: number;
  employee_count: number;
  processed_at: string;
}

export interface PayrollHistoryResponse {
  entries: PayrollHistoryEntry[];
  total: number;
}

let accessTokenGetter: () => string | null = () => null;

export function setAccessTokenGetter(getter: () => string | null): void {
  accessTokenGetter = getter;
}

const BASE = "/api/v1/employees";

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
  // 204 No Content (e.g. DELETE) has no body to parse.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listEmployees(
  params: { department?: string; active?: boolean; search?: string; skip?: number; limit?: number } = {},
): Promise<EmployeeListResponse> {
  const query = new URLSearchParams();
  if (params.department) query.set("department", params.department);
  if (params.active !== undefined) query.set("active", String(params.active));
  if (params.search) query.set("search", params.search);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 100));
  return request<EmployeeListResponse>(`/?${query.toString()}`);
}

export function employeeOptions(): Promise<EmployeeOptions> {
  return request<EmployeeOptions>("/options");
}

export function createEmployee(payload: EmployeeCreatePayload): Promise<Employee> {
  return request<Employee>("/", { method: "POST", body: JSON.stringify(payload) });
}

export function updateEmployee(employeeId: string, payload: EmployeeUpdatePayload): Promise<Employee> {
  return request<Employee>(`/${employeeId}`, { method: "PUT", body: JSON.stringify(payload) });
}

/** Only succeeds for an employee with no payroll history yet — the backend
 *  returns 409 otherwise and asks for `active: false` instead, so real
 *  payroll history is never silently destroyed alongside the row. */
export function deleteEmployee(employeeId: string): Promise<void> {
  return request<void>(`/${employeeId}`, { method: "DELETE" });
}

export function payrollSummary(): Promise<PayrollSummary> {
  return request<PayrollSummary>("/payroll");
}

export function processPayroll(): Promise<PayrollProcessResult> {
  return request<PayrollProcessResult>("/payroll/process", { method: "POST" });
}

export function payrollHistory(params: { skip?: number; limit?: number } = {}): Promise<PayrollHistoryResponse> {
  const query = new URLSearchParams();
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 12));
  return request<PayrollHistoryResponse>(`/payroll/history?${query.toString()}`);
}
