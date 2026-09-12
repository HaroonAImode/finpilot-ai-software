import { useEffect, useRef, useState, type ChangeEventHandler, type ComponentType, type ReactNode } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  Archive,
  Bell,
  Bot,
  Boxes,
  ChartColumnBig,
  ChevronsUpDown,
  FilePlus2,
  FileText,
  FolderOpen,
  LayoutDashboard,
  Menu,
  PanelLeft,
  PanelLeftClose,
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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

/** The sidebar/badge categorical system (DESIGN.md's "Color Categorical
 * System") — each business area gets one deep jewel hue, applied to its
 * pages' leading icon chip. Dashboard and Settings are deliberately
 * uncategorized (utility pages, not a feature domain), so they render a
 * neutral chip instead of claiming a jewel tone. */
type NavCategory = "teal" | "violet" | "amber" | "navy";

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  exact?: boolean;
}

const dashboardItem: NavItem = { to: "/app", label: "Dashboard", icon: LayoutDashboard, exact: true };
const settingsItem: NavItem = { to: "/app/settings", label: "Settings", icon: Settings };

/** Grouped sidebar structure — this IS the source of truth `navItems`
 * below is flattened from, so the grouped sidebar and the ⌘K command
 * palette can never drift out of sync with each other. */
const navGroups: { label: string; category: NavCategory; items: NavItem[] }[] = [
  {
    label: "Documents",
    category: "teal",
    items: [
      { to: "/app/scanner", label: "Invoice Scanner", icon: ScanLine },
      { to: "/app/records", label: "Saved Records", icon: Archive },
      { to: "/app/documents", label: "Documents", icon: FolderOpen },
    ],
  },
  {
    label: "Finance",
    category: "violet",
    items: [
      { to: "/app/invoices", label: "Invoice Generator", icon: FilePlus2 },
      { to: "/app/revenue", label: "Revenue Manager", icon: TrendingUp },
      { to: "/app/expenses", label: "Expenses", icon: Receipt },
    ],
  },
  {
    label: "Operations",
    category: "amber",
    items: [
      { to: "/app/employees", label: "Employees", icon: Users },
      { to: "/app/procurement", label: "Procurement", icon: Boxes },
      { to: "/app/vendors", label: "Vendors", icon: Store },
      { to: "/app/vendor-reconciliation", label: "Vendor Reconciliation", icon: ShieldCheck },
    ],
  },
  {
    label: "Insights",
    category: "navy",
    items: [
      { to: "/app/analytics", label: "Analytics", icon: ChartColumnBig },
      { to: "/app/assistant", label: "AI Assistant", icon: Bot },
      { to: "/app/reports", label: "Reports", icon: FileText },
    ],
  },
];

export const navItems = [dashboardItem, ...navGroups.flatMap((g) => g.items), settingsItem];

/** A page's leading icon — a small colored chip for a categorized page
 * (self-contained contrast: a solid fill with a white glyph on top, so it
 * never depends on the sidebar's own background to stay legible), or a
 * plain neutral square for an uncategorized utility page. */
function NavIcon({
  icon: Icon,
  category,
}: {
  icon: ComponentType<{ className?: string }>;
  category?: NavCategory;
}) {
  if (!category) {
    return (
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-sidebar-accent text-sidebar-accent-foreground">
        <Icon className="h-3.5 w-3.5" />
      </span>
    );
  }
  return (
    <span className="category-chip h-7 w-7 shrink-0" data-category={category}>
      <Icon className="h-3.5 w-3.5" />
    </span>
  );
}

function NavRow({ item, onNavigate }: { item: NavItem; onNavigate?: (() => void) | undefined; category?: NavCategory }) {
  return (
    <Link
      to={item.to}
      onClick={onNavigate}
      activeOptions={{ exact: item.exact ?? false }}
      activeProps={{ className: "bg-sidebar-accent text-sidebar-accent-foreground font-semibold" }}
      inactiveProps={{
        className: "text-sidebar-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
      }}
      className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm transition-colors"
    >
      <NavIcon icon={item.icon} />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

/** The workspace-identity header at the top of the sidebar — company name
 * + signed-in email, the whole block opening the same account menu
 * (Settings/Sign out) the top header's avatar does. Mirrors a premium
 * workspace switcher's placement without inventing a second workspace to
 * switch to (this app has exactly one). */
function WorkspaceHeader() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function signOut() {
    await logout();
    await navigate({ to: "/login" });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex w-full items-center gap-2.5 rounded-xl p-1.5 text-left outline-none transition-colors hover:bg-sidebar-accent/60 focus-visible:ring-2 focus-visible:ring-ring">
          <span className="gradient-brand grid h-8 w-8 shrink-0 place-items-center rounded-lg text-primary-foreground">
            <Wallet className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-semibold leading-tight text-sidebar-foreground">
              {user?.company_name?.trim() || "FinPilot AI"}
            </span>
            <span className="block truncate text-[11px] text-sidebar-muted-foreground">{user?.email}</span>
          </span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-sidebar-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <p className="truncate text-sm font-medium">{user?.full_name ?? "Signed in"}</p>
          <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
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

function SidebarBody({ onNavigate }: { onNavigate?: (() => void) | undefined }) {
  return (
    <div className="flex h-full flex-col gap-1 p-3">
      <WorkspaceHeader />

      <div className="mt-2 mb-1">
        <NavRow item={dashboardItem} onNavigate={onNavigate} />
      </div>

      <nav className="sidebar-scroll flex flex-1 flex-col gap-4 overflow-y-auto pb-2">
        {navGroups.map((group) => (
          <div key={group.label}>
            <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-sidebar-muted-foreground/80">
              {group.label}
            </p>
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <NavRow key={item.to} item={item} onNavigate={onNavigate} category={group.category} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-2 border-t border-sidebar-border pt-2">
        <NavRow item={settingsItem} onNavigate={onNavigate} />
        {/* Solid violet — the same deep hue as the progress bar's own fill,
         * not a pale wash — a deliberately bolder "highlighted callout"
         * treatment for the one card in the sidebar meant to stand out.
         * White text/icon throughout, since the card's own background is
         * now the dark color that would otherwise swallow ink-toned text. */}
        <div className="rounded-xl bg-[var(--color-category-violet)] p-3.5">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-white/15 text-white">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          <p className="mt-2.5 text-sm font-semibold text-white">AI Credits</p>
          <p className="mt-1 text-xs text-white/70">824 of 1,000 invoice scans left this month.</p>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-white/20">
            <div className="h-full w-[82%] rounded-full bg-white" />
          </div>
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

const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 420;
const SIDEBAR_DEFAULT_WIDTH = 256;
const SIDEBAR_WIDTH_KEY = "finpilot-sidebar-width";
const SIDEBAR_COLLAPSED_KEY = "finpilot-sidebar-collapsed";

/** Persisted sidebar width + collapsed state — a workspace-shaped app like
 *  this one gets used for hours at a stretch, so "how wide I like the rail"
 *  and "whether I've hidden it" are exactly the kind of per-user setting
 *  that should survive a reload, not reset every visit. */
function useSidebarPrefs() {
  const [width, setWidthState] = useState(SIDEBAR_DEFAULT_WIDTH);
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    try {
      const storedWidth = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
      if (storedWidth >= SIDEBAR_MIN_WIDTH && storedWidth <= SIDEBAR_MAX_WIDTH) {
        setWidthState(storedWidth);
      }
      setCollapsedState(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true");
    } catch {
      // Private-browsing/storage-blocked — the defaults above are already
      // applied; there's just nothing to remember between visits.
    }
  }, []);

  const setWidth = (next: number) => {
    const clamped = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(next)));
    setWidthState(clamped);
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
    } catch {}
  };

  const toggleCollapsed = () => {
    setCollapsedState((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch {}
      return next;
    });
  };

  return { width, collapsed, setWidth, toggleCollapsed };
}

/** The thin draggable strip on the sidebar's own right edge — a real drag,
 *  not a fixed set of preset widths, the same "drag to resize" affordance
 *  a native panel (or Notion's/Linear's own sidebar) gives you. Only
 *  rendered when the sidebar isn't collapsed — nothing to resize while
 *  hidden. */
function SidebarResizeHandle({ onResize }: { onResize: (width: number) => void }) {
  const draggingRef = useRef(false);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    draggingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };
  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    // The aside's own left edge is pinned at x=0 (fixed inset-y-0 left-0),
    // so the pointer's clientX *is* the new width directly — no delta
    // bookkeeping needed, and no drift if a move event is ever missed.
    onResize(e.clientX);
  };
  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  };

  return (
    <TooltipProvider delayDuration={400}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            className="group absolute inset-y-0 -right-1.5 z-10 w-3 cursor-col-resize touch-none"
          >
            <div className="mx-auto h-full w-px bg-transparent transition-colors group-hover:bg-primary/50 group-active:bg-primary" />
          </div>
        </TooltipTrigger>
        <TooltipContent side="right">Drag to resize</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function AppShell() {
  const [open, setOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const { width: sidebarWidth, collapsed: sidebarCollapsed, setWidth: setSidebarWidth, toggleCollapsed } =
    useSidebarPrefs();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const current = navItems.find((i) => i.to === pathname)?.label ?? "Dashboard";

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key.toLowerCase() === "b" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleCollapsed();
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
    // toggleCollapsed is a fresh closure each render (the hook holds no
    // memoized identity), but the shortcut only ever needs to fire the
    // *current* toggle — re-subscribing this cheap a listener on every
    // render is not worth memoizing the hook around.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="min-h-screen w-full bg-background"
      style={{ ["--sidebar-w" as string]: `${sidebarCollapsed ? 0 : sidebarWidth}px` }}
    >
      <aside
        className="fixed inset-y-0 left-0 z-30 hidden overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:block"
        style={{
          width: "var(--sidebar-w)",
          transition: "width 150ms ease",
        }}
      >
        {/* A fixed-width inner wrapper — the sidebar's own content never
         *  reflows/squishes as the outer `<aside>` animates toward 0 while
         *  collapsing; it just slides out of view. */}
        <div style={{ width: sidebarWidth }} className="h-full">
          <SidebarBody />
        </div>
        {!sidebarCollapsed && <SidebarResizeHandle onResize={setSidebarWidth} />}
      </aside>

      {/* The collapsed sidebar's own re-expand control — sits where the
       *  sidebar's left edge used to be, only shown once there's no
       *  sidebar there to click into instead. */}
      {sidebarCollapsed && (
        <TooltipProvider delayDuration={400}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={toggleCollapsed}
                aria-label="Show sidebar"
                className="fixed left-3 top-3 z-30 hidden h-8 w-8 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground shadow-sm transition-colors hover:text-foreground lg:flex"
              >
                <PanelLeft className="h-4 w-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              Show sidebar <kbd className="ml-1 opacity-70">Ctrl B</kbd>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      <div className="lg:pl-[var(--sidebar-w)]" style={{ transition: "padding-left 150ms ease" }}>
        <header className="glass sticky top-0 z-20 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Sheet open={mobileNav} onOpenChange={setMobileNav}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
                  <Menu className="h-4 w-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-72 bg-sidebar p-0 text-sidebar-foreground">
                <SheetTitle className="sr-only">Navigation</SheetTitle>
                <SidebarBody onNavigate={() => setMobileNav(false)} />
              </SheetContent>
            </Sheet>
            {!sidebarCollapsed && (
              <TooltipProvider delayDuration={400}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="hidden lg:inline-flex"
                      onClick={toggleCollapsed}
                      aria-label="Hide sidebar"
                    >
                      <PanelLeftClose className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    Hide sidebar <kbd className="ml-1 opacity-70">Ctrl B</kbd>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
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
  Pending: "bg-warning/15 text-warning border-warning/30",
  "Pending Approval": "bg-warning/15 text-warning border-warning/30",
  Processing: "bg-accent text-accent-foreground border-accent-foreground/20",
  "In Transit": "bg-accent text-accent-foreground border-accent-foreground/20",
  "Needs Review": "bg-accent text-accent-foreground border-accent-foreground/20",
  Review: "bg-accent text-accent-foreground border-accent-foreground/20",
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
