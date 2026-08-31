import { useEffect, useState } from "react";

/**
 * Turns a fetched Blob into a same-origin object URL for `<img src>`/
 * `<iframe src>`, revoking the previous one whenever the blob changes or
 * the component unmounts. Shared by every place that previews a document's
 * bytes (invoice thumbnails/preview, page-picker thumbnails) — the same
 * five lines were being copied into each one.
 */
export function useObjectUrl(blob: Blob | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return;
    }
    const created = URL.createObjectURL(blob);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [blob]);

  return url;
}
