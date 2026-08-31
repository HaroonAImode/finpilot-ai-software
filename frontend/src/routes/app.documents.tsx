import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ArrowLeft, Grid2x2, LayoutList, List, Search, Waypoints } from "lucide-react";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SourceColumn } from "@/components/documents/source-column";
import { SourceFolderCard } from "@/components/documents/source-folder-card";
import { DocumentPreviewDialog } from "@/components/documents/document-preview-dialog";
import { BulkActionsBar } from "@/components/documents/bulk-actions-bar";
import {
  DOCUMENT_CATEGORIES, DOCUMENT_SOURCES, type DocumentSourceId, type DocumentView,
  type FileBrowserViewMode, type UnifiedDocument,
} from "@/lib/documents";

export const Route = createFileRoute("/app/documents")({
  head: () => ({
    meta: [
      { title: "Documents — FinPilot AI" },
      {
        name: "description",
        content:
          "Every document FinPilot has collected — from Slack, Email, WhatsApp, or uploaded straight from your browser — organized into folders, one source at a time.",
      },
      { property: "og:title", content: "Documents — FinPilot AI" },
    ],
  }),
  component: DocumentsPage,
});

function DocumentsPage() {
  // null = the folder grid (the landing view). Set = browsing one source's
  // files full-width, the way opening a folder replaces a file manager's
  // "This PC" view rather than adding a fifth column next to four others.
  const [openSourceId, setOpenSourceId] = useState<DocumentSourceId | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | undefined>(undefined);
  // Grouped is the more useful default: it is what actually answers "where did
  // this come from", which is the reason this view exists. Flat stays one
  // click away for a simple newest-first scan across everything.
  const [view, setView] = useState<DocumentView>("grouped");
  const [fileViewMode, setFileViewMode] = useState<FileBrowserViewMode>("list");
  const [previewDocument, setPreviewDocument] = useState<UnifiedDocument | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  /** Keyed by id so the bulk bar has each document's full data (source, category,
   *  eligibility) without re-querying, and selection survives a column refetch. */
  const [selected, setSelected] = useState<Map<string, UnifiedDocument>>(new Map());

  const openSource = DOCUMENT_SOURCES.find((s) => s.id === openSourceId) ?? null;

  const openPreview = (doc: UnifiedDocument) => {
    setPreviewDocument(doc);
    setPreviewOpen(true);
  };

  const toggleSelect = (doc: UnifiedDocument) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(doc.id)) next.delete(doc.id);
      else next.set(doc.id, doc);
      return next;
    });
  };

  const selectMany = (documents: UnifiedDocument[], shouldSelect: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const doc of documents) {
        if (shouldSelect) next.set(doc.id, doc);
        else next.delete(doc.id);
      }
      return next;
    });
  };

  const clearSelection = () => setSelected(new Map());

  // Drop only what actually succeeded, so a partial batch leaves the failures
  // still selected and ready to retry.
  const dropSucceeded = (ids: string[]) => {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  };

  const backToFolders = () => {
    setOpenSourceId(null);
    clearSelection();
    setSearch("");
    setCategory(undefined);
  };

  const selectedDocuments = [...selected.values()];

  return (
    <TooltipProvider>
      <PageHeader
        title="Documents"
        subtitle={
          openSource
            ? `Browsing ${openSource.label} — search, switch views, or head back to the other folders.`
            : "Everything FinPilot has collected from your connected apps and your own uploads — pick a folder to open it."
        }
        actions={
          openSource ? (
            <Button variant="ghost" className="gap-1.5 rounded-xl" onClick={backToFolders}>
              <ArrowLeft className="h-4 w-4" /> All Folders
            </Button>
          ) : undefined
        }
      />

      {!openSource && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {DOCUMENT_SOURCES.map((source) => (
            <SourceFolderCard key={source.id} source={source} onOpen={() => setOpenSourceId(source.id)} />
          ))}
        </div>
      )}

      {openSource && (
        <>
          <div className="surface mb-5 flex flex-col gap-3 p-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${openSource.label} by name`}
                className="rounded-xl border-none bg-muted/40 pl-9 focus-visible:ring-1"
              />
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Select
                value={category ?? "all"}
                onValueChange={(value) => setCategory(value === "all" ? undefined : value)}
              >
                <SelectTrigger className="w-full rounded-xl sm:w-56">
                  <SelectValue placeholder="All categories" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All categories</SelectItem>
                  {DOCUMENT_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                </SelectContent>
              </Select>

              {openSource.fetchConversations && (
                <ToggleGroup
                  type="single"
                  value={view}
                  onValueChange={(value) => value && setView(value as DocumentView)}
                  className="rounded-xl border p-0.5"
                >
                  <ToggleGroupItem value="grouped" className="gap-1.5 rounded-lg text-xs" aria-label="Group by conversation">
                    <Waypoints className="h-3.5 w-3.5" /> By conversation
                  </ToggleGroupItem>
                  <ToggleGroupItem value="flat" className="gap-1.5 rounded-lg text-xs" aria-label="Flat list">
                    <LayoutList className="h-3.5 w-3.5" /> Flat
                  </ToggleGroupItem>
                </ToggleGroup>
              )}

              {/* Only meaningful in flat mode — a grouped column still shows
                  each conversation's files as plain rows (see source-column's
                  own docstring for why that scope boundary is deliberate). */}
              {(!openSource.fetchConversations || view === "flat") && (
                <ToggleGroup
                  type="single"
                  value={fileViewMode}
                  onValueChange={(value) => value && setFileViewMode(value as FileBrowserViewMode)}
                  className="rounded-xl border p-0.5"
                >
                  <ToggleGroupItem value="list" className="rounded-lg" aria-label="List view" title="List">
                    <List className="h-3.5 w-3.5" />
                  </ToggleGroupItem>
                  <ToggleGroupItem value="small-icons" className="rounded-lg" aria-label="Small icons" title="Small icons">
                    <Grid2x2 className="h-3.5 w-3.5" />
                  </ToggleGroupItem>
                  <ToggleGroupItem value="large-icons" className="rounded-lg" aria-label="Large icons" title="Large icons">
                    <Grid2x2 className="h-4.5 w-4.5" />
                  </ToggleGroupItem>
                </ToggleGroup>
              )}
            </div>
          </div>

          <div className="h-[calc(100vh-280px)] min-h-[28rem]">
            <SourceColumn
              source={openSource}
              category={category}
              search={search}
              view={view}
              viewMode={fileViewMode}
              onPreview={openPreview}
              selectedIds={new Set(selected.keys())}
              onToggleSelect={toggleSelect}
              onSelectMany={selectMany}
            />
          </div>

          {selectedDocuments.length > 0 && (
            <BulkActionsBar
              selected={selectedDocuments}
              onClear={clearSelection}
              onDone={dropSucceeded}
            />
          )}
        </>
      )}

      <DocumentPreviewDialog
        document={previewDocument}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
      />
    </TooltipProvider>
  );
}
