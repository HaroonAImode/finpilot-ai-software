/**
 * The actual answer to "20k-30k documents must not slow the page down or
 * require endless scrolling to reach the end"
 * (docs/superpowers/specs/2026-09-01-documents-folder-browser-design.md §5):
 *
 * - Only the rows currently on screen (plus a small overscan buffer) are
 *   ever mounted in the DOM, regardless of how many items exist —
 *   `@tanstack/react-virtual`'s row-based windowing. A 30,000-file folder
 *   costs the same DOM size as a 30-file one.
 * - Data itself is paginated (`onEndReached`, wired to `useInfiniteQuery`
 *   in source-column.tsx) — the page only ever asks the server for the
 *   next page once the user actually scrolls near the end of what has
 *   already loaded, never "give me all 30,000 rows up front."
 *
 * Works for both the plain list (columns=1) and the icon-grid views
 * (columns=N) by treating each virtualized "row" as a strip of `columns`
 * items — the standard react-virtual grid recipe.
 */
import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

export function VirtualizedFileBrowser<T>({
  items, getId, renderItem, columns, rowHeight, onEndReached, hasMore = false, isFetchingMore = false,
  className,
}: {
  items: T[];
  getId: (item: T) => string;
  renderItem: (item: T) => React.ReactNode;
  columns: number;
  rowHeight: number;
  /** Called once when the user has scrolled near the last loaded row —
   *  the caller is responsible for actually fetching the next page and
   *  for `hasMore`/`isFetchingMore` reflecting that fetch's real state, so
   *  this never fires twice for the same page. */
  onEndReached?: () => void;
  hasMore?: boolean;
  isFetchingMore?: boolean;
  className?: string;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const rowCount = Math.ceil(items.length / columns);

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 6,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();
  const lastRow = virtualRows[virtualRows.length - 1];

  useEffect(() => {
    if (!lastRow || !hasMore || isFetchingMore || !onEndReached) return;
    // Within 3 rows of the end of what is already loaded — fetch the next
    // page before the user actually hits the bottom, so scrolling never
    // visibly stalls waiting on a network round trip.
    if (lastRow.index >= rowCount - 3) onEndReached();
  }, [lastRow, rowCount, hasMore, isFetchingMore, onEndReached]);

  return (
    <div ref={parentRef} className={`overflow-y-auto ${className ?? ""}`}>
      <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {virtualRows.map((virtualRow) => {
          const start = virtualRow.index * columns;
          const rowItems = items.slice(start, start + columns);
          return (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={rowVirtualizer.measureElement}
              className={columns > 1 ? "grid gap-2" : ""}
              style={{
                position: "absolute", top: 0, left: 0, width: "100%",
                transform: `translateY(${virtualRow.start}px)`,
                ...(columns > 1 ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } : {}),
              }}
            >
              {rowItems.map((item) => <div key={getId(item)}>{renderItem(item)}</div>)}
            </div>
          );
        })}
      </div>
      {isFetchingMore && (
        <p className="py-3 text-center text-xs text-muted-foreground">Loading more…</p>
      )}
    </div>
  );
}
