export const money = (n: number) =>
  "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });

export const kpis = [
  { label: "Today's Revenue", value: 486_400, delta: 12.4, hint: "vs yesterday", icon: "wallet" },
  { label: "Monthly Revenue", value: 9_840_000, delta: 18.2, hint: "vs last month", icon: "trending" },
  { label: "Monthly Expenses", value: 6_120_000, delta: -5.1, hint: "vs last month", icon: "receipt" },
  { label: "Net Profit", value: 3_720_000, delta: 22.8, hint: "vs last month", icon: "piggy" },
  { label: "Pending Invoices", value: 34, delta: -8.3, hint: "awaiting review", icon: "clock", raw: true },
  { label: "Processed Invoices", value: 1_286, delta: 9.6, hint: "this month", icon: "check", raw: true },
  { label: "Employee Salaries", value: 2_180_000, delta: 3.2, hint: "July payroll", icon: "users" },
  { label: "Cash Balance", value: 14_260_000, delta: 6.7, hint: "all accounts", icon: "bank" },
];

export const revenueVsExpenses = [
  { month: "Jan", revenue: 6_200_000, expenses: 4_400_000 },
  { month: "Feb", revenue: 6_850_000, expenses: 4_720_000 },
  { month: "Mar", revenue: 7_400_000, expenses: 5_180_000 },
  { month: "Apr", revenue: 7_120_000, expenses: 5_340_000 },
  { month: "May", revenue: 8_260_000, expenses: 5_600_000 },
  { month: "Jun", revenue: 8_940_000, expenses: 5_910_000 },
  { month: "Jul", revenue: 9_840_000, expenses: 6_120_000 },
];

export const expenseCategories = [
  { name: "Salaries", value: 2_180_000 },
  { name: "Raw Material", value: 1_540_000 },
  { name: "Fuel & Transport", value: 780_000 },
  { name: "Utilities", value: 640_000 },
  { name: "Rent", value: 520_000 },
  { name: "Marketing", value: 460_000 },
];

export const revenueSources = [
  { name: "Retail", value: 3_420_000 },
  { name: "Wholesale", value: 2_860_000 },
  { name: "Online", value: 1_940_000 },
  { name: "Exports", value: 1_120_000 },
  { name: "Services", value: 500_000 },
];

export const cashFlow = [
  { month: "Jan", inflow: 6_400_000, outflow: 4_600_000 },
  { month: "Feb", inflow: 6_900_000, outflow: 4_850_000 },
  { month: "Mar", inflow: 7_600_000, outflow: 5_250_000 },
  { month: "Apr", inflow: 7_050_000, outflow: 5_480_000 },
  { month: "May", inflow: 8_400_000, outflow: 5_720_000 },
  { month: "Jun", inflow: 9_100_000, outflow: 6_010_000 },
  { month: "Jul", inflow: 9_950_000, outflow: 6_240_000 },
];

export const topVendors = [
  { name: "ABC Traders", value: 1_240_000 },
  { name: "Karachi Steel Co.", value: 980_000 },
  { name: "Lahore Packaging", value: 760_000 },
  { name: "Sindh Fuels Ltd.", value: 540_000 },
  { name: "Rehman Logistics", value: 410_000 },
];

export const invoiceStatus = [
  { name: "Processed", value: 1286 },
  { name: "Pending", value: 34 },
  { name: "Needs Review", value: 18 },
  { name: "Duplicate", value: 7 },
];

export type InvoiceStatus = "Processed" | "Pending" | "Duplicate" | "Needs Review";

export const recentInvoices: {
  no: string;
  vendor: string;
  date: string;
  amount: number;
  status: InvoiceStatus;
}[] = [
  { no: "INV-2026-1841", vendor: "ABC Traders", date: "04 Aug 2026", amount: 184_500, status: "Processed" },
  { no: "INV-2026-1840", vendor: "Karachi Steel Co.", date: "04 Aug 2026", amount: 96_200, status: "Pending" },
  { no: "INV-2026-1839", vendor: "Lahore Packaging", date: "03 Aug 2026", amount: 42_800, status: "Needs Review" },
  { no: "INV-2026-1838", vendor: "Sindh Fuels Ltd.", date: "03 Aug 2026", amount: 68_400, status: "Processed" },
  { no: "INV-2026-1837", vendor: "Rehman Logistics", date: "02 Aug 2026", amount: 27_900, status: "Duplicate" },
  { no: "INV-2026-1836", vendor: "Metro Stationers", date: "02 Aug 2026", amount: 12_450, status: "Processed" },
  { no: "INV-2026-1835", vendor: "Faisalabad Textiles", date: "01 Aug 2026", amount: 312_000, status: "Pending" },
];

export const employees = [
  { name: "Ayesha Khan", dept: "Finance", salary: 185_000, bonus: 20_000, deductions: 12_000, status: "Paid" },
  { name: "Bilal Ahmed", dept: "Operations", salary: 150_000, bonus: 10_000, deductions: 8_500, status: "Paid" },
  { name: "Hina Siddiqui", dept: "Sales", salary: 135_000, bonus: 32_000, deductions: 9_000, status: "Processing" },
  { name: "Usman Tariq", dept: "Procurement", salary: 128_000, bonus: 8_000, deductions: 7_200, status: "Paid" },
  { name: "Sana Malik", dept: "Accounts", salary: 118_000, bonus: 6_000, deductions: 6_400, status: "Pending" },
  { name: "Fahad Rehman", dept: "IT", salary: 165_000, bonus: 15_000, deductions: 10_000, status: "Paid" },
  { name: "Zara Iqbal", dept: "HR", salary: 112_000, bonus: 5_000, deductions: 5_800, status: "Pending" },
  { name: "Imran Shah", dept: "Logistics", salary: 98_000, bonus: 4_000, deductions: 4_900, status: "Paid" },
];

export const vendors = [
  { name: "ABC Traders", category: "Raw Material", city: "Karachi", spend: 1_240_000, rating: 4.8, terms: "Net 30", status: "Active" },
  { name: "Karachi Steel Co.", category: "Steel", city: "Karachi", spend: 980_000, rating: 4.5, terms: "Net 45", status: "Active" },
  { name: "Lahore Packaging", category: "Packaging", city: "Lahore", spend: 760_000, rating: 4.2, terms: "Net 15", status: "Active" },
  { name: "Sindh Fuels Ltd.", category: "Fuel", city: "Hyderabad", spend: 540_000, rating: 3.9, terms: "Advance", status: "Review" },
  { name: "Rehman Logistics", category: "Transport", city: "Multan", spend: 410_000, rating: 4.6, terms: "Net 30", status: "Active" },
  { name: "Metro Stationers", category: "Office", city: "Islamabad", spend: 180_000, rating: 4.0, terms: "Net 7", status: "Inactive" },
];

export const expenses = [
  { id: "EXP-4412", category: "Fuel & Transport", vendor: "Sindh Fuels Ltd.", date: "04 Aug 2026", amount: 68_400, method: "Bank Transfer", status: "Approved" },
  { id: "EXP-4411", category: "Salaries", vendor: "Payroll July", date: "01 Aug 2026", amount: 2_180_000, method: "Bank Transfer", status: "Approved" },
  { id: "EXP-4410", category: "Utilities", vendor: "K-Electric", date: "31 Jul 2026", amount: 214_800, method: "Online", status: "Approved" },
  { id: "EXP-4409", category: "Raw Material", vendor: "ABC Traders", date: "30 Jul 2026", amount: 484_500, method: "Cheque", status: "Pending" },
  { id: "EXP-4408", category: "Marketing", vendor: "Digital Reach", date: "29 Jul 2026", amount: 126_000, method: "Card", status: "Approved" },
  { id: "EXP-4407", category: "Rent", vendor: "Gulshan Plaza", date: "28 Jul 2026", amount: 520_000, method: "Bank Transfer", status: "Approved" },
  { id: "EXP-4406", category: "Office", vendor: "Metro Stationers", date: "27 Jul 2026", amount: 12_450, method: "Cash", status: "Rejected" },
];

export const purchaseRequests = [
  { id: "PR-2041", item: "Steel Sheets (2 tons)", dept: "Production", requester: "Usman Tariq", amount: 640_000, status: "Pending Approval" },
  { id: "PR-2040", item: "Packaging Rolls", dept: "Warehouse", requester: "Imran Shah", amount: 148_000, status: "Approved" },
  { id: "PR-2039", item: "Office Laptops (4)", dept: "IT", requester: "Fahad Rehman", amount: 720_000, status: "Pending Approval" },
  { id: "PR-2038", item: "Diesel Refill", dept: "Logistics", requester: "Imran Shah", amount: 96_000, status: "Delayed" },
];

export const purchaseOrders = [
  { id: "PO-7712", vendor: "Karachi Steel Co.", date: "02 Aug 2026", amount: 980_000, delivery: "10 Aug 2026", status: "In Transit" },
  { id: "PO-7711", vendor: "Lahore Packaging", date: "31 Jul 2026", amount: 148_000, delivery: "05 Aug 2026", status: "Delivered" },
  { id: "PO-7710", vendor: "ABC Traders", date: "28 Jul 2026", amount: 484_500, delivery: "03 Aug 2026", status: "Delivered" },
  { id: "PO-7709", vendor: "Sindh Fuels Ltd.", date: "26 Jul 2026", amount: 96_000, delivery: "29 Jul 2026", status: "Cancelled" },
];

export const vendorComparison = [
  { vendor: "ABC Traders", price: 312_000, delivery: "3 days", quality: "A+", terms: "Net 30", score: 94 },
  { vendor: "Karachi Steel Co.", price: 298_000, delivery: "6 days", quality: "A", terms: "Net 45", score: 88 },
  { vendor: "Punjab Metals", price: 330_000, delivery: "2 days", quality: "A+", terms: "Net 15", score: 85 },
];

export const insights = [
  { title: "Revenue increased 18%", body: "July revenue reached PKR 9.84M, driven by retail and online channels.", tone: "positive" },
  { title: "Salaries consume 36% of expenses", body: "Payroll is the largest expense block at PKR 2.18M this month.", tone: "neutral" },
  { title: "Fuel spending increased 22%", body: "Transport costs rose sharply — review Sindh Fuels Ltd. contract terms.", tone: "warning" },
  { title: "Top vendor: ABC Traders", body: "PKR 1.24M spent across 28 invoices. Eligible for a 4% volume discount.", tone: "neutral" },
  { title: "Cash flow is healthy", body: "Net positive cash flow of PKR 3.71M with 4.2 months of runway buffer.", tone: "positive" },
];

export const upcomingPayments = [
  { title: "Karachi Steel Co.", due: "08 Aug", amount: 980_000 },
  { title: "K-Electric bill", due: "12 Aug", amount: 214_800 },
  { title: "Gulshan Plaza rent", due: "15 Aug", amount: 520_000 },
  { title: "August payroll", due: "01 Sep", amount: 2_180_000 },
];

export const notifications = [
  { title: "3 invoices need review", body: "AI confidence below 85% on 3 uploads.", time: "12m ago" },
  { title: "Duplicate invoice detected", body: "INV-2026-1837 matches an earlier upload.", time: "1h ago" },
  { title: "Payroll processed", body: "July salaries disbursed to 42 employees.", time: "3h ago" },
  { title: "New purchase request", body: "PR-2041 awaits your approval.", time: "Yesterday" },
];

export const topCustomers = [
  { name: "Al-Madina Retail", value: 1_860_000 },
  { name: "Shaheen Distributors", value: 1_420_000 },
  { name: "Green Valley Foods", value: 1_180_000 },
  { name: "Pak Mart Chain", value: 940_000 },
  { name: "Noor Enterprises", value: 720_000 },
];

export const forecast = [
  { month: "Aug", actual: 9_840_000, projected: 10_250_000 },
  { month: "Sep", actual: null, projected: 10_780_000 },
  { month: "Oct", actual: null, projected: 11_320_000 },
  { month: "Nov", actual: null, projected: 11_900_000 },
  { month: "Dec", actual: null, projected: 12_640_000 },
];

export const profitMargin = [
  { month: "Feb", margin: 31 },
  { month: "Mar", margin: 30 },
  { month: "Apr", margin: 25 },
  { month: "May", margin: 32 },
  { month: "Jun", margin: 34 },
  { month: "Jul", margin: 38 },
];

export const extractedInvoice = {
  vendor: "ABC Traders",
  number: "INV-2026-1841",
  date: "2026-08-04",
  ntn: "3947261-8",
  items: [
    { desc: "Steel Sheet 4mm", qty: 12, rate: 9_800 },
    { desc: "Welding Rods (box)", qty: 6, rate: 3_200 },
    { desc: "Transport Charges", qty: 1, rate: 8_500 },
  ],
  taxRate: 18,
};

export const extractedSale = {
  customer: "Al-Madina Retail",
  invoice: "SAL-2026-0912",
  date: "2026-08-04",
  method: "Bank Transfer",
  products: [
    { desc: "Basmati Rice 25kg", qty: 40, rate: 6_400 },
    { desc: "Cooking Oil 5L", qty: 60, rate: 2_150 },
    { desc: "Sugar 50kg", qty: 15, rate: 8_900 },
  ],
  taxRate: 18,
  status: "Paid",
};
