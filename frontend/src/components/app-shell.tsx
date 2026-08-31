import { useEffect, useState, type ChangeEventHandler, type ReactNode } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  Archive,
  Bell,
  Bot,
  Boxes,
  ChartColumnBig,
  FilePlus2,
  FileText,
  FolderOpen,
  LayoutDashboard,
  Menu,
  Receipt,
  ShieldCheck,
  ScanLine,
  Search,
  Settings,
  Sparkles,
  Store,
  TrendingUp,
  Upload,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth-context";
import { notifications } from "@/lib/data";
import { cn } from "@/lib/utils";
import { NeedsReviewCenter } from "@/components/invoices/needs-review-center";

/** Signed-in identity and sign-out. Replaces the hardcoded "AK" avatar. */
function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const initials =
    (user?.full_name ?? "")
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?";

  async function signOut() {
    await logout();
    await navigate({ to: "/login" });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className="ml-1 rounded-full outline-none ring-offset-2 focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Account menu"
        >
          <Avatar className="h-9 w-9 border border-border">
            <AvatarFallback className="bg-[image:var(--gradient-brand)] text-xs font-semibold text-primary-foreground">
              {initials}
            </AvatarFallback>
          </Avatar>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <p className="truncate text-sm font-medium">{user?.full_name ?? "Signed in"}</p>
          <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
          {user?.company_name && (
            <p className="mt-1 truncate text-xs text-muted-foreground">{user.company_name}</p>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/app/settings">Settings</Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void signOut()}>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export const navItems = [
  { to: "/app", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/app/scanner", label: "Invoice Scanner", icon: ScanLine },
  { to: "/app/records", label: "Saved Records", icon: Archive },
  { to: "/app/documents", label: "Documents", icon: FolderOpen },
  { to: "/app/invoices", label: "Invoice Generator", icon: FilePlus2 },
  { to: "/app/revenue", label: "Revenue Manager", icon: TrendingUp },
  { to: "/app/expenses", label: "Expenses", icon: Receipt },
  { to: "/app/employees", label: "Employees", icon: Users },
  { to: "/app/procurement", label: "Procurement", icon: Boxes },
  { to: "/app/vendors", label: "Vendors", icon: Store },
  { to: "/app/vendor-reconciliation", label: "Vendor Reconciliation", icon: ShieldCheck },
  { to: "/app/analytics", label: "Analytics", icon: ChartColumnBig },
  { to: "/app/assistant", label: "AI Assistant", icon: Bot },
  { to: "/app/reports", label: "Reports", icon: FileText },
  { to: "/app/settings", label: "Settings", icon: Settings },
] as const;

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <span className="gradient-brand grid h-9 w-9 shrink-0 place-items-center rounded-xl text-primary-foreground shadow-[var(--shadow-soft)]">
        <Wallet className="h-4.5 w-4.5" />
      </span>
      <span className="min-w-0">
        <span className="block truncate font-display text-[15px] font-bold leading-tight">
          FinPilot AI
        </span>
        <span className="block truncate text-[11px] text-muted-foreground">
          Accounting Automation
        </span>
      </span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: (() => void) | undefined }) {
  return (
    <nav className="flex flex-col gap-1">
      {navItems.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          onClick={onNavigate}
          activeOptions={{ exact: "exact" in item ? item.exact : false }}
          activeProps={{
            className: "bg-primary/10 text-primary font-semibold",
          }}
          inactiveProps={{
            className: "text-muted-foreground hover:bg-muted hover:text-foreground",
          }}
          className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors"
        >
          <item.icon className="h-4 w-4 shrink-0" />
          <span className="truncate">{item.label}</span>
        </Link>
      ))}
    </nav>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: (() => void) | undefined }) {
  return (
    <div className="flex h-full flex-col gap-6 p-4">
      <div className="px-1 pt-1">
        <Brand />
      </div>
      <NavLinks onNavigate={onNavigate} />
      <div className="mt-auto rounded-2xl border border-border bg-[image:var(--gradient-soft)] p-4">
        <Sparkles className="h-4 w-4 text-primary" />
        <p className="mt-2 text-sm font-semibold">AI Credits</p>
        <p className="mt-1 text-xs text-muted-foreground">
          824 of 1,000 invoice scans left this month.
        </p>
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div className="h-full w-[82%] rounded-full bg-[image:var(--gradient-brand)]" />
        </div>
      </div>
    </div>
  );
}

function NotificationPanel() {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label="Notifications">
          <Bell className="h-4 w-4" />
          <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-destructive" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-4 py-3">
          <p className="text-sm font-semibold">Notifications</p>
          <p className="text-xs text-muted-foreground">4 new updates</p>
        </div>
        <ul className="max-h-80 divide-y overflow-auto">
          {notifications.map((n) => (
            <li key={n.title} className="px-4 py-3 transition-colors hover:bg-muted/60">
              <p className="text-sm font-medium">{n.title}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{n.body}</p>
              <p className="mt-1 text-[11px] text-muted-foreground/80">{n.time}</p>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

export function AppShell() {
  const [open, setOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const current = navItems.find((i) => i.to === pathname)?.label ?? "Dashboard";

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  return (
    <div className="min-h-screen w-full bg-background">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-sidebar-border bg-sidebar lg:block">
        <SidebarBody />
      </aside>

      <div className="lg:pl-64">
        <header className="glass sticky top-0 z-20 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Sheet open={mobileNav} onOpenChange={setMobileNav}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
                  <Menu className="h-4 w-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-72 bg-sidebar p-0">
                <SheetTitle className="sr-only">Navigation</SheetTitle>
                <SidebarBody onNavigate={() => setMobileNav(false)} />
              </SheetContent>
            </Sheet>
            <button
              onClick={() => setOpen(true)}
              className="hidden w-full max-w-sm items-center gap-2 rounded-xl border border-border bg-background/60 px-3 py-2 text-sm text-muted-foreground transition-colors hover:border-primary/40 sm:flex"
            >
              <Search className="h-4 w-4" />
              <span className="truncate">Search invoices, vendors, reports…</span>
              <kbd className="ml-auto rounded-md border border-border px-1.5 py-0.5 text-[10px]">
                ⌘K
              </kbd>
            </button>
            <span className="truncate font-display text-base font-semibold sm:hidden">
              {current}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="ghost"
              size="icon"
              className="sm:hidden"
              onClick={() => setOpen(true)}
              aria-label="Search"
            >
              <Search className="h-4 w-4" />
            </Button>
            <ThemeToggle />
            <NeedsReviewCenter />
            <NotificationPanel />
            <Button asChild className="hidden gap-2 rounded-xl sm:inline-flex">
              <Link to="/app/scanner">
                <Upload className="h-4 w-4" /> Upload Invoice
              </Link>
            </Button>
            <UserMenu />
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 sm:py-8">
          <Outlet />
        </main>
      </div>

      <Link
        to="/app/assistant"
        className="fixed bottom-6 right-6 z-30 flex items-center gap-2 rounded-full bg-[image:var(--gradient-brand)] px-5 py-3.5 text-sm font-semibold text-primary-foreground shadow-[var(--shadow-lift)] transition-transform hover:scale-105"
      >
        <Sparkles className="h-4 w-4" /> Ask AI
      </Link>

      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder="Search pages, invoices, vendors…" />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Pages">
            {navItems.map((item) => (
              <CommandItem key={item.to} value={item.label} onSelect={() => setOpen(false)} asChild>
                <Link to={item.to} className="flex items-center gap-2">
                  <item.icon className="h-4 w-4" /> {item.label}
                </Link>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandGroup heading="Quick actions">
            <CommandItem onSelect={() => setOpen(false)}>Upload new invoice</CommandItem>
            <CommandItem onSelect={() => setOpen(false)}>
              Generate Profit &amp; Loss report
            </CommandItem>
            <CommandItem onSelect={() => setOpen(false)}>Run payroll for August</CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4 sm:flex sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h1 className="truncate font-display text-2xl font-bold sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

const statusTones: Record<string, string> = {
  Processed: "bg-success/12 text-success border-success/25",
  Paid: "bg-success/12 text-success border-success/25",
  Approved: "bg-success/12 text-success border-success/25",
  Delivered: "bg-success/12 text-success border-success/25",
  Active: "bg-success/12 text-success border-success/25",
  Pending: "bg-warning/15 text-warning-foreground border-warning/30 dark:text-warning",
  "Pending Approval": "bg-warning/15 text-warning-foreground border-warning/30 dark:text-warning",
  Processing: "bg-primary/12 text-primary border-primary/25",
  "In Transit": "bg-primary/12 text-primary border-primary/25",
  "Needs Review": "bg-primary/12 text-primary border-primary/25",
  Review: "bg-primary/12 text-primary border-primary/25",
  "Needs Review · Priority": "bg-destructive/12 text-destructive border-destructive/25",
  Validated: "bg-success/12 text-success border-success/25",
  "Sent to Accounting": "bg-success/12 text-success border-success/25",
  Duplicate: "bg-destructive/12 text-destructive border-destructive/25",
  Cancelled: "bg-destructive/12 text-destructive border-destructive/25",
  Rejected: "bg-destructive/12 text-destructive border-destructive/25",
  Delayed: "bg-destructive/12 text-destructive border-destructive/25",
  Inactive: "bg-muted text-muted-foreground border-border",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-full px-2.5 py-0.5 text-[11px] font-medium",
        statusTones[status] ?? "bg-muted",
      )}
    >
      {status}
    </Badge>
  );
}

export function SearchField({
  placeholder,
  value,
  onChange,
}: {
  placeholder: string;
  //  Both optional — every existing call site is a decorative, uncontrolled
  //  field and keeps working unchanged. A caller that wants the typed value
  //  (Expenses' vendor search) passes both to make it controlled.
  value?: string;
  onChange?: ChangeEventHandler<HTMLInputElement>;
}) {
  return (
    <div className="relative w-full sm:w-64">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        className="rounded-xl pl-9"
      />
    </div>
  );
}

export { X };
