import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { fetchInvoiceContent } from "@/lib/invoice-service";

/**
 * Shows the actual uploaded document next to its extracted fields — the
 * scanner review panel's "show exactly what was extracted from where"
 * grounding (Rule 8.2). Deliberately lighter than
 * documents/document-preview-dialog.tsx: no docx/html conversion, since an
 * invoice upload is always a PDF or image (ocr lib's own UnsupportedFileType
 * guard already refuses anything else at scan time).
 */
export function InvoiceOriginalPreview({
  invoiceId, mimetype, filename,
}: { invoiceId: string; mimetype: string | null; filename: string }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setObjectUrl(null);
    setError(null);

    fetchInvoiceContent(invoiceId)
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || "Could not load this document");
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [invoiceId]);

  const isImage = mimetype?.startsWith("image/") ?? false;

  return (
    <div className="mt-4 aspect-[4/3] overflow-hidden rounded-xl border bg-muted/40">
      {error ? (
        <div className="grid h-full place-items-center gap-2 text-center text-sm text-muted-foreground">
          <AlertCircle className="mx-auto h-6 w-6 text-destructive" />
          <p>{error}</p>
        </div>
      ) : !objectUrl ? (
        <div className="grid h-full place-items-center text-sm text-muted-foreground">Loading preview…</div>
      ) : isImage ? (
        <img src={objectUrl} alt={filename} className="h-full w-full object-contain" />
      ) : (
        <iframe src={objectUrl} title={filename} className="h-full w-full border-0" />
      )}
    </div>
  );
}
