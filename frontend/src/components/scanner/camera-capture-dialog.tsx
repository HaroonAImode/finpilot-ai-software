/**
 * Live camera capture for the Scanner page.
 *
 * Flow: capture → client-side blur check → confirm screen
 * (preview + optional manual crop + Category/Payment Method) → Upload.
 *
 * Auto-crop was removed: the server-side preprocess_image pipeline takes
 * 2-3 minutes to respond, which made the capture flow unusable. Manual
 * crop (crop-adjuster.tsx) gives the user full control with zero latency.
 *
 * Re-crop UX: the original capture blob is kept separately from the
 * "current preview" blob. Opening the crop tool a second time always shows
 * the original image with the previously-applied crop box pre-loaded, so
 * the user can widen or narrow their earlier selection freely.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, Crop, Flashlight, RotateCcw, Upload, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { invoiceCategoryOptions, type PaymentMethod } from "@/lib/invoice-service";
import { computeBlurVariance, isBlurry } from "@/lib/blur-detection";
import { applyCropToImage, CropAdjuster, type CropBox } from "@/components/scanner/crop-adjuster";

const UNCATEGORIZED = "Uncategorized";
const PAYMENT_METHOD_OPTIONS: PaymentMethod[] = ["cash", "bank"];
const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = { cash: "Cash", bank: "Online" };

type Screen = "live" | "confirm" | "manual-crop";

export function CameraCaptureDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (file: File, category: string | null, paymentMethod: PaymentMethod | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const [screen, setScreen] = useState<Screen>("live");
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [retakeReason, setRetakeReason] = useState<string | null>(null);
  const [torchOn, setTorchOn] = useState(false);

  // The blob to upload — starts as the raw capture, replaced by the
  // cropped result when the user applies a crop.
  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null);

  // The URL shown in the confirm-screen preview (may be cropped).
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // The raw capture's object URL — never overwritten after capture. Passed
  // to CropAdjuster so re-cropping always starts from the full original
  // photo, not an already-cropped slice.
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);

  // The last crop box applied, so re-opening the adjuster pre-loads it.
  const [appliedCropBox, setAppliedCropBox] = useState<CropBox | null>(null);

  const [category, setCategory] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">("");

  const categoryOptionsQuery = useQuery({
    queryKey: ["invoice-options"],
    queryFn: invoiceCategoryOptions,
  });

  const stopCamera = useCallback(() => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (track) {
      try {
        (track as any).applyConstraints?.({ advanced: [{ torch: false }] });
      } catch {}
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setTorchOn(false);
  }, []);

  const toggleTorch = async () => {
    const next = !torchOn;
    setTorchOn(next);
    const track = streamRef.current?.getVideoTracks()[0];
    if (track) {
      try {
        await (track as any).applyConstraints?.({ advanced: [{ torch: next }] });
      } catch (err) {
        console.warn("Hardware torch not supported or permission denied", err);
      }
    }
  };

  // Camera is on only while the dialog is open on the live screen — torn
  // down the moment we leave it so the camera-light never confuses the user.
  useEffect(() => {
    if (!open || screen !== "live") {
      stopCamera();
      return;
    }

    let cancelled = false;
    setCameraError(null);

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
      })
      .catch(() => {
        if (!cancelled)
          setCameraError("Could not access the camera — check your browser's camera permission.");
      });

    return () => {
      cancelled = true;
      stopCamera();
    };
  }, [open, screen, stopCamera]);

  // Revoke the preview object URL when it changes, to avoid memory leaks.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  // Revoke the original URL only when it changes (i.e. on new capture).
  useEffect(() => {
    return () => {
      if (originalUrl) URL.revokeObjectURL(originalUrl);
    };
  }, [originalUrl]);

  /** Replace the current preview with a new blob, revoking the old URL. */
  const setPreview = (blob: Blob) => {
    setCapturedBlob(blob);
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(blob);
    });
  };

  const handleCapture = () => {
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) return;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (torchOn) {
      ctx.filter = "brightness(1.18) contrast(1.08)";
    }
    ctx.drawImage(video, 0, 0);
    const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);

    // Client-side blur check — runs instantly, before the user even sees
    // the preview. Only genuine motion blur or out-of-focus shots fail.
    const gray = new Uint8ClampedArray(frame.width * frame.height);
    for (let i = 0; i < gray.length; i++) {
      const r = frame.data[i * 4]!,
        g = frame.data[i * 4 + 1]!,
        b = frame.data[i * 4 + 2]!;
      gray[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    }
    if (isBlurry(computeBlurVariance(gray, frame.width, frame.height))) {
      setRetakeReason("This photo looks blurry — hold the camera steady and try again.");
      return;
    }
    setRetakeReason(null);

    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        // Store the original blob URL separately — never touched after this.
        const rawUrl = URL.createObjectURL(blob);
        setOriginalUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return rawUrl;
        });
        setAppliedCropBox(null);
        setPreview(blob);
        setScreen("confirm");
      },
      "image/jpeg",
      0.92,
    );
  };

  const handleRetake = () => {
    setCapturedBlob(null);
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setOriginalUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setAppliedCropBox(null);
    setScreen("live");
  };

  /** Apply a manual crop: crop the *original* image (not the last preview)
   *  using the box, then store both the new preview and the box itself so
   *  re-opening the adjuster starts from the right state. */
  const handleApplyManualCrop = async (box: CropBox) => {
    if (!originalUrl) return;
    try {
      const cropped = await applyCropToImage(originalUrl, box);
      setAppliedCropBox(box);
      setPreview(cropped);
    } finally {
      setScreen("confirm");
    }
  };

  const handleUpload = () => {
    if (!capturedBlob) return;
    const file = new File([capturedBlob], `camera-capture-${Date.now()}.jpg`, {
      type: "image/jpeg",
    });
    onConfirm(file, category || null, paymentMethod || null);
    handleClose();
  };

  const handleClose = () => {
    stopCamera();
    setScreen("live");
    setCapturedBlob(null);
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setOriginalUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setAppliedCropBox(null);
    setCategory("");
    setPaymentMethod("");
    setRetakeReason(null);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : handleClose())}>
      <DialogContent className="max-w-lg">
        <DialogTitle>
          {screen === "live" && "Scan with Camera"}
          {screen === "confirm" && "Confirm Capture"}
          {screen === "manual-crop" && "Adjust Crop"}
        </DialogTitle>

        {/* ── Live camera screen ──────────────────────────────────────── */}
        {screen === "live" && (
          <div className="flex flex-col gap-3">
            {cameraError ? (
              <p className="rounded-xl bg-destructive/10 p-4 text-sm text-destructive">
                {cameraError}
              </p>
            ) : (
              <div className="relative overflow-hidden rounded-xl bg-black">
                <video
                  ref={videoRef}
                  autoPlay
                  playsInline
                  muted
                  className="w-full transition-[filter] duration-200"
                  style={{ filter: torchOn ? "brightness(1.18) contrast(1.08)" : "none" }}
                />

                {/* Torch / Flashlight toggle button */}
                <button
                  type="button"
                  onClick={toggleTorch}
                  aria-label={torchOn ? "Turn flashlight off" : "Turn flashlight on"}
                  className={`absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold backdrop-blur shadow-md transition-all active:scale-95 ${
                    torchOn
                      ? "bg-amber-400 text-black shadow-amber-400/40 ring-2 ring-amber-300 font-bold"
                      : "bg-black/70 text-white hover:bg-black/90 ring-1 ring-white/25"
                  }`}
                >
                  {torchOn ? (
                    <>
                      <Zap className="h-3.5 w-3.5 fill-current text-black" />
                      <span>Flash: On</span>
                    </>
                  ) : (
                    <>
                      <Flashlight className="h-3.5 w-3.5 text-white" />
                      <span>Flash: Off</span>
                    </>
                  )}
                </button>
              </div>
            )}
            {retakeReason && (
              <p className="rounded-xl bg-warning/15 p-3 text-sm font-medium text-warning">
                {retakeReason}
              </p>
            )}
            <p className="text-center text-xs text-muted-foreground">
              Frame the document so all edges are visible, then capture.
            </p>
            <Button className="gap-2 rounded-xl" disabled={!!cameraError} onClick={handleCapture}>
              <Camera className="h-4 w-4" /> Capture
            </Button>
          </div>
        )}

        {/* ── Manual crop adjuster ────────────────────────────────────── */}
        {screen === "manual-crop" && originalUrl && (
          <CropAdjuster
            imageUrl={originalUrl}
            {...(appliedCropBox ? { initialBox: appliedCropBox } : {})}
            onApply={handleApplyManualCrop}
            onCancel={() => setScreen("confirm")}
          />
        )}

        {/* ── Confirm screen ──────────────────────────────────────────── */}
        {screen === "confirm" && previewUrl && (
          <div className="flex flex-col gap-4">
            {/* Preview image + Crop button */}
            <div className="relative overflow-hidden rounded-xl border bg-black">
              <img
                src={previewUrl}
                alt="Captured document"
                className="max-h-64 w-full object-contain"
              />
              {/* Crop button — dark bg so it's always visible over any image */}
              <button
                type="button"
                onClick={() => setScreen("manual-crop")}
                className="absolute right-2 top-2 flex items-center gap-1.5 rounded-lg bg-black/75 px-2.5 py-1.5 text-xs font-semibold text-white shadow-md backdrop-blur-sm transition-colors hover:bg-black/90"
                title="Adjust crop"
              >
                <Crop className="h-3.5 w-3.5" />
                Crop
              </button>
            </div>

            {/* Category + Payment Method */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Category</Label>
                <Select
                  value={category || UNCATEGORIZED}
                  onValueChange={(v) => setCategory(v === UNCATEGORIZED ? "" : v)}
                >
                  <SelectTrigger className="rounded-xl">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={UNCATEGORIZED}>{UNCATEGORIZED}</SelectItem>
                    {(categoryOptionsQuery.data?.categories ?? []).map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Payment Method</Label>
                <div className="flex gap-2">
                  {PAYMENT_METHOD_OPTIONS.map((method) => (
                    <button
                      key={method}
                      type="button"
                      aria-pressed={paymentMethod === method}
                      onClick={() => setPaymentMethod(method)}
                      className={`flex-1 rounded-full border px-3 py-2 text-sm font-medium transition-colors ${
                        paymentMethod === method
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
                      }`}
                    >
                      {PAYMENT_METHOD_LABELS[method]}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Action row */}
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1 gap-2 rounded-xl" onClick={handleRetake}>
                <RotateCcw className="h-4 w-4" /> Retake
              </Button>
              <Button className="flex-1 gap-2 rounded-xl" onClick={handleUpload}>
                <Upload className="h-4 w-4" /> Upload
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
