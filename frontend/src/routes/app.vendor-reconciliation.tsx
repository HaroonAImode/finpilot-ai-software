import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronsUpDown, EyeOff, Plus, ShieldCheck, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/app-shell";
import { toast } from "sonner";
import {
  createVendorAndLinkGroup, ignoreVendorName, linkGroupToVendor, listIgnoredVendorNames,
  listVendors, reconciliationQueue, unignoreVendorName, vendorOptions,
  type ReconciliationGroup, type Vendor,
} from "@/lib/vendors-service";

export const Route = createFileRoute("/app/vendor-reconciliation")({
  head: () => ({
    meta: [
      { title: "Vendor Reconciliation — FinPilot AI" },
      {
        name: "description",
        content: "Turn raw OCR vendor names into real, deduplicated vendor records.",
      },
    ],
  }),
  component: VendorReconciliationPage,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

/** A searchable picker over the existing vendor directory — for when a raw
 *  name's suggestions are wrong or empty and the accountant knows which
 *  vendor it actually is. */
function VendorPicker({
  vendors, onSelect, placeholder = "Search vendors…",
}: {
  vendors: Vendor[];
  onSelect: (vendor: Vendor) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="w-full justify-between gap-2 rounded-lg font-normal">
          <span className="truncate text-muted-foreground">{placeholder}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-0">
        <Command>
          <CommandInput placeholder="Type a vendor name…" />
          <CommandList>
            <CommandEmpty>No vendors match.</CommandEmpty>
            <CommandGroup>
              {vendors.map((v) => (
                <CommandItem
                  key={v.id}
                  value={v.name}
                  onSelect={() => {
                    onSelect(v);
                    setOpen(false);
                  }}
                >
                  {v.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function NewVendorDialog({
  group, open, onOpenChange, onCreated,
}: {
  group: ReconciliationGroup | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [city, setCity] = useState("");
  const [ntn, setNtn] = useState("");

  const optionsQuery = useQuery({ queryKey: ["vendor-options"], queryFn: vendorOptions, enabled: open });

  // Reseed the form's default name every time a different group opens it.
  const groupName = group?.vendor_name ?? "";
  const [seededFor, setSeededFor] = useState("");
  if (open && groupName !== seededFor) {
    setName(groupName);
    setCategory("");
    setCity("");
    setNtn("");
    setSeededFor(groupName);
  }

  const mutation = useMutation({
    mutationFn: createVendorAndLinkGroup,
    onSuccess: (result) => {
      toast.success(`Created vendor and linked ${result.linked_count} invoice(s)`);
      onCreated();
      onOpenChange(false);
    },
    onError: (error: Error) => toast.error(error.message || "Could not create this vendor"),
  });

  if (!group) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New vendor from "{group.vendor_name}"</DialogTitle>
          <DialogDescription>
            Creates the vendor and links all {group.invoice_count} invoice(s) to it in one step.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Vendor name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} className="rounded-xl" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Category</Label>
              <Input
                list="reconciliation-categories"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="Optional"
                className="rounded-xl"
              />
              <datalist id="reconciliation-categories">
                {(optionsQuery.data?.categories ?? []).map((c) => <option key={c} value={c} />)}
              </datalist>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">City</Label>
              <Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Optional" className="rounded-xl" />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">NTN</Label>
            <Input value={ntn} onChange={(e) => setNtn(e.target.value)} placeholder="Optional" className="rounded-xl" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" className="rounded-xl" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            className="rounded-xl"
            disabled={mutation.isPending || !name.trim()}
            onClick={() =>
              mutation.mutate({
                vendor_name: group.vendor_name, invoice_ids: group.invoice_ids,
                name: name.trim(), category: category || null, city: city.trim() || null, ntn: ntn.trim() || null,
              })
            }
          >
            {mutation.isPending ? "Creating…" : "Create & link"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function GroupCard({
  group, vendors, onChanged,
}: {
  group: ReconciliationGroup;
  vendors: Vendor[];
  onChanged: () => void;
}) {
  const [newVendorOpen, setNewVendorOpen] = useState(false);

  const linkMutation = useMutation({
    mutationFn: linkGroupToVendor,
    onSuccess: (result) => {
      toast.success(`Linked ${result.linked_count} invoice(s)`);
      onChanged();
    },
    onError: (error: Error) => toast.error(error.message || "Could not link this group"),
  });

  const ignoreMutation = useMutation({
    mutationFn: ignoreVendorName,
    onSuccess: () => {
      toast.success(`"${group.vendor_name}" won't appear here again`);
      onChanged();
    },
    onError: (error: Error) => toast.error(error.message || "Could not ignore this name"),
  });

  const linkTo = (vendorId: string) =>
    linkMutation.mutate({ vendor_name: group.vendor_name, invoice_ids: group.invoice_ids, vendor_id: vendorId });

  const busy = linkMutation.isPending || ignoreMutation.isPending;

  return (
    <div className="surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-base font-semibold">{group.vendor_name}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {group.invoice_count} invoice{group.invoice_count === 1 ? "" : "s"} · {money(group.total_amount)}
            {group.sample_invoice_number ? ` · e.g. ${group.sample_invoice_number}` : ""}
            {group.latest_invoice_date ? ` · latest ${group.latest_invoice_date}` : ""}
          </p>
        </div>
        <Button
          variant="ghost" size="sm" className="gap-1.5 text-muted-foreground" disabled={busy}
          onClick={() => ignoreMutation.mutate(group.vendor_name)}
        >
          <EyeOff className="h-3.5 w-3.5" /> Not a vendor
        </Button>
      </div>

      {group.suggestions.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {group.suggestions.map((s) => (
            <Button
              key={s.vendor_id}
              variant="outline"
              size="sm"
              disabled={busy}
              className="gap-1.5 rounded-lg border-primary/30 bg-primary/5 text-primary hover:bg-primary/10"
              onClick={() => linkTo(s.vendor_id)}
            >
              <Check className="h-3.5 w-3.5" /> {s.name}
              <Badge variant="secondary" className="ml-1 rounded-full px-1.5 py-0 text-[10px]">
                {Math.round(s.score * 100)}% match
              </Badge>
            </Button>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="w-56">
          <VendorPicker
            vendors={vendors}
            placeholder="Or pick a different vendor…"
            onSelect={(v) => linkTo(v.id)}
          />
        </div>
        <Button
          variant="outline" size="sm" className="gap-1.5 rounded-lg" disabled={busy}
          onClick={() => setNewVendorOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" /> This is a new vendor
        </Button>
      </div>

      <NewVendorDialog
        group={group}
        open={newVendorOpen}
        onOpenChange={setNewVendorOpen}
        onCreated={onChanged}
      />
    </div>
  );
}

function IgnoredNamesPopover({ onChanged }: { onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const ignoredQuery = useQuery({
    queryKey: ["vendor-reconciliation", "ignored"],
    queryFn: listIgnoredVendorNames,
    enabled: open,
  });

  const unignoreMutation = useMutation({
    mutationFn: unignoreVendorName,
    onSuccess: () => {
      toast.success("Restored to the queue");
      ignoredQuery.refetch();
      onChanged();
    },
    onError: (error: Error) => toast.error(error.message || "Could not restore this name"),
  });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5 rounded-xl">
          <EyeOff className="h-3.5 w-3.5" /> Ignored names
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <p className="mb-2 text-xs font-medium text-muted-foreground">
          Dismissed as "not a vendor" — restore one if that was a mistake.
        </p>
        {ignoredQuery.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : (ignoredQuery.data ?? []).length === 0 ? (
          <p className="py-3 text-center text-xs text-muted-foreground">Nothing ignored yet.</p>
        ) : (
          <ul className="max-h-64 space-y-1 overflow-y-auto">
            {ignoredQuery.data!.map((entry) => (
              <li key={entry.id} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-muted/50">
                <span className="truncate">{entry.vendor_name}</span>
                <Button
                  variant="ghost" size="icon" className="h-6 w-6 shrink-0"
                  disabled={unignoreMutation.isPending}
                  onClick={() => unignoreMutation.mutate(entry.id)}
                >
                  <Undo2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

function VendorReconciliationPage() {
  const queryClient = useQueryClient();

  const queueQuery = useQuery({ queryKey: ["vendor-reconciliation", "queue"], queryFn: reconciliationQueue });
  const vendorsQuery = useQuery({
    queryKey: ["vendors", "all-for-picker"],
    queryFn: () => listVendors({ limit: 200 }),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["vendor-reconciliation"] });
    queryClient.invalidateQueries({ queryKey: ["vendors"] });
  };

  const groups = queueQuery.data?.groups ?? [];
  const vendors = vendorsQuery.data?.vendors ?? [];

  return (
    <>
      <PageHeader
        title="Vendor Reconciliation"
        subtitle={
          queueQuery.isPending
            ? "Loading…"
            : queueQuery.isError
              ? "Could not load the queue"
              : `${groups.length} vendor name${groups.length === 1 ? "" : "s"} awaiting a decision`
        }
        actions={<IgnoredNamesPopover onChanged={refresh} />}
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted-foreground">
        Every scanned invoice arrives with a vendor name written by OCR, not a linked vendor
        record. Decide once per name here — link it, ignore it, or record it as a new
        vendor — and every invoice that shares it is resolved together.
      </p>

      {queueQuery.isPending || vendorsQuery.isPending ? (
        <div className="mt-6 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32 w-full rounded-xl" />)}
        </div>
      ) : queueQuery.isError ? (
        <div className="surface mt-6 flex flex-col items-center gap-2 p-10 text-center">
          <AlertTriangle className="h-8 w-8 text-destructive" />
          <p className="font-medium">{(queueQuery.error as Error).message || "Invoice Service could not be reached"}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            This queue is built from live invoice data, so it can't be shown as empty when it
            simply couldn't be fetched. Try again shortly.
          </p>
          <Button variant="outline" className="mt-2 gap-1.5 rounded-xl" onClick={() => queueQuery.refetch()}>
            Retry
          </Button>
        </div>
      ) : groups.length === 0 ? (
        <div className="surface mt-6 flex flex-col items-center gap-2 p-10 text-center">
          <ShieldCheck className="h-8 w-8 text-success" />
          <p className="font-medium">All caught up</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Every scanned invoice is linked to a vendor record. New scans will appear here if
            they can't be matched automatically.
          </p>
        </div>
      ) : (
        <div className="mt-6 space-y-4">
          {groups.map((group) => (
            <GroupCard key={group.vendor_name} group={group} vendors={vendors} onChanged={refresh} />
          ))}
        </div>
      )}
    </>
  );
}
