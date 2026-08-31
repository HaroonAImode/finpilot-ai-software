# AI Invoice Scanner — Live Camera Capture

**Status:** Design — for review before implementation, per the user's explicit
"give me a proper plan before adding" instruction.

## 1. Summary

Today, an accounting-department user holding a physical invoice/bill/receipt has to leave the
web app entirely: take a photo with the phone's own camera app, then either email/AirDrop/Slack
it to a desktop, or switch to the desktop themselves, download it, and only then use the
Scanner page's "Browse files" button. Every step in that chain is dead time this app cannot see
or help with, and it happens dozens of times a day in a real accounting department.

This feature adds a **Camera** button next to Browse/WhatsApp/Slack/Email on `/app/scanner`
that opens the phone's (or laptop's) camera live, right inside the app, shows a real-time
bounding-box overlay around the document as the user frames the shot — the same live-guidance
experience CamScanner/Adobe Scan give — captures the shot, rejects it on the spot with a plain-
English reason if it's blurry or the document isn't fully in frame (never silently accepting a
bad photo that would just come back `needs_review` anyway), shows a small preview with exactly
two fields to set (Category, Payment Method), and then sends it through the **exact same
`scanInvoice()` upload path** every other source already uses — no new backend endpoint, no new
processing pipeline, no second way an invoice gets created.

**The one architectural fact this whole design leans on:** `backend/libs/ocr/ocr/preprocess.py`
already exists, is already wired into every image upload through the Scanner today, and already
does exactly the "detect the document, crop out the background noise, split multiple documents"
job CamScanner's own smart-crop does — server-side, well-tested, and with an explicit design
philosophy this feature inherits rather than reinvents (§3.1). The camera feature's job is
narrower and genuinely new: get a *good enough* photo into that existing pipeline, fast, with
live feedback a static file-picker can never give.

## 2. Goals

1. **A camera option that actually replaces the phone-photo-then-email workflow.** Not a toy —
   after this ships, a real accounting-department user should never need to leave the browser
   tab to get a physical document into FinPilot.
2. **Live guidance while framing the shot**, so the user knows *before* pressing capture whether
   the document is actually in frame — a transparent, live-updating bounding box drawn over the
   detected document edges on the video preview itself, matching the professional-scanner-app
   feel the user explicitly asked for.
3. **Reject bad captures immediately, with a specific reason**, before a single byte reaches the
   network: too blurry to read, or the document isn't fully captured. "Please retake — the photo
   looks blurry" is a concrete, actionable message; a generic upload failure or a silent
   `needs_review` days later is not.
4. **A minimal confirm step, not a full edit form.** A small preview thumbnail plus exactly the
   two fields the user named as actually necessary at capture time — Category, Payment Method —
   then Upload. The full field-by-field review (vendor, totals, line items, …) still happens
   exactly where it already does today, after extraction, on the same Scanner page.
5. **Zero new backend surface.** The captured photo becomes a `File`/`Blob` exactly like a
   browsed one and goes through `scanInvoice()` unchanged. Every existing guarantee (dedup,
   confidence-gated auto-process/needs-review routing, document preprocessing, arithmetic
   validation) applies automatically, because it is the same code path.
6. **Fast.** Live detection must run smoothly on a mid-range phone, not lag behind the camera
   feed. This bounds the technology choice in §3.2 — a fast approximation while framing, with
   the already-existing higher-effort server-side pass as the authority once a shot is taken.

## 3. Architecture

### 3.1 Detection happens twice, on purpose, and they are not the same algorithm

There are two genuinely different jobs here, and conflating them is exactly the mistake this
design avoids:

| | **Live overlay (client, in the video feed)** | **Authoritative crop (server, already built)** |
|---|---|---|
| Job | Show the user roughly where the document is *while they're still framing the shot*, so they can adjust before pressing capture | Decide the final crop that actually gets fed to OCR |
| Where | Browser, on every preview frame | `ocr/preprocess.py`, already called from `ocr/extract.py`'s `extract_documents_with_engine()` for every image upload, unchanged |
| Budget | Must run continuously without visibly lagging the camera feed on a mid-range phone | Runs once, after capture, has a full second or two to be careful |
| Failure mode if wrong | Mildly annoying (box a little off) — the user can still see the live feed and just isn't relying on the box alone | Consequential — `preprocess.py`'s own documented rule is "when detection is not confident, do nothing... A wrong crop can permanently cut off a total" |

**This feature only builds the left column.** The right column already exists, is already
tested, and already runs on every image this feature will ever produce, with no change needed —
because the captured photo is uploaded through `scanInvoice()` exactly like a browsed file, and
`extract_documents_with_engine()` does not know or care whether an image came from a camera or a
file picker.

### 3.2 Live overlay: technology choice

Real-time, in-browser document-edge detection is a solved problem with an established open
pattern: decode each preview frame to grayscale, blur it, run Canny edge detection, find
contours, approximate the largest plausible one to a quadrilateral, draw it. **OpenCV.js**
(OpenCV compiled to WebAssembly) is the standard tool for this — it's what
[jscanify](https://scanbot.io/techblog/js-camera-document-scanner-tutorial/) and multiple
browser document-scanner projects are built on, and OpenCV's own engineering blog covers this
exact "smart document scanning" pattern. Confirmed workable at real-time video rates with no
noticeable latency when used carefully.

Two concrete decisions that follow from "must be fast," not just "must work":

- **Lazy-load OpenCV.js only when the Camera option is opened**, never on initial page load. The
  WASM bundle is a real ~8MB — that's an acceptable cost the moment a user has chosen to scan by
  camera, and an unacceptable one added to the page every single visit.
- **Throttle detection, not the video feed.** The `<video>` element itself renders at full
  native frame rate always — only the *analysis* (grab a frame → OpenCV contour pass → redraw
  the overlay box) runs on a fixed interval (roughly 150ms), not on every frame. The user's eyes
  can't tell the difference between an overlay updated 6-7 times a second and one updated 30
  times a second, and the CPU cost difference on a mid-range phone is real.
- **The live overlay is a simplified version of `preprocess.py`'s own first stage** (grayscale →
  blur → threshold/Canny → contour → rectangularity check) — deliberately not the full module
  (no multi-document valley-split, no PNG re-encode, no safety-padding math): those exist to get
  a *final* crop right; the overlay only exists to give live visual feedback while framing.

### 3.3 Client-side quality gate — the "please retake" checks

Run once, right after the shutter fires, before the confirm screen ever appears:

1. **Blur check — variance of the Laplacian.** This is the standard, well-established technique
   for exactly this ([PyImageSearch](https://pyimagesearch.com/2015/09/07/blur-detection-with-opencv/),
   and the broader CV literature): convolve the captured frame with a Laplacian kernel and take
   the variance of the result. A sharp photo has many strong edges and a high variance; a
   blurry one has few and a low one. Cheap to compute (OpenCV.js is already loaded for the live
   overlay by this point) and needs no network round-trip to answer "is this readable."
2. **Framing/completeness check — reuses the same contour pass the live overlay already runs.**
   At the moment of capture, look at the best detected document contour: if none was found with
   real confidence, or the one found touches too close to the frame's own edge on more than one
   side (a strong signal the document extends past what the camera captured), or it covers too
   small a share of the frame, reject with a specific reason rather than uploading a photo the
   server-side pipeline would likely fail on anyway.
3. **Both checks fail closed toward asking the user to retake, not toward silently uploading.**
   This mirrors `preprocess.py`'s own stated philosophy exactly (§3.1), applied to a different
   decision: a false "please retake" costs the user three seconds; a photo that was genuinely
   too blurry to read costs a document stuck in `needs_review` days later with no obvious reason
   why. Thresholds are tuned to be forgiving, not paranoid — normal handheld phone-camera photos
   in ordinary indoor lighting must pass comfortably; only genuinely bad captures (heavy motion
   blur, a document clearly still moving out of frame, one edge plainly out of shot) get rejected.

On rejection: show the specific reason as plain text ("This photo looks blurry — hold the camera
steady and try again." / "The document doesn't look fully in frame — make sure all four edges
are visible.") and return straight to the live camera view, not to a dead-end error screen.

### 3.4 Post-capture confirm screen

- A single still preview of the captured (already client-cropped-to-the-detected-quad, for
  display purposes only — the server crop is still authoritative) frame, small, not full-screen.
- Exactly two fields: **Category** (the same suggestion list `invoiceCategoryOptions()` already
  serves, same component this session already added to the Scanner's post-extraction form) and
  **Payment Method** (Cash/Online, the same two-option pill picker the Scanner's edit form
  already uses). No vendor, no totals, no date — those are extracted automatically once the
  upload happens, exactly as they already are for every other source.
- Two actions: **Retake** (discard, back to live camera) and **Upload** (send).
- **Upload calls `scanInvoice(file)` exactly as today**, then — once the resulting invoice
  exists — issues one `updateInvoice(id, { category, payment_method })` call to apply the two
  fields the user just set, reusing the existing `updateInvoice` mutation unchanged. This is the
  same two-step "create, then patch" shape the rest of this codebase already uses (e.g. Saved
  Records' own inline category/payment-method editors), not a new endpoint.
- From here, everything is identical to today: the scan mutation's existing success/needs-review
  toast, the existing multi-document-detected toast, the invoice appearing in the paginated
  table this session already built, with its thumbnail, hover preview, and "open in Saved
  Records" button all already working unchanged.

### 3.5 Explicitly deferred, with reasons (not silently dropped)

- **Manual crop/corner adjustment.** The user asked for this, and it's a legitimate professional-
  scanner feature — but a real perspective-correcting quad-crop editor (drag four corners,
  re-warp the image) is a genuinely separate, non-trivial UI component, and `preprocess.py`'s
  own server-side crop is already good (well-tested against real receipts, biased toward *not*
  cropping wrongly). **Recommendation: ship Phase 1 without it** — auto-detect overlay + retake
  is the safety net — and add a manual adjustment step only if real usage shows the auto-crop
  needs correcting often enough to justify it. Flagging this explicitly for your decision rather
  than assuming either way.
- **Desktop webcam support.** The design works on desktop (the exact same `getUserMedia` API),
  but is explicitly optimized and tested for a phone's rear camera first, per the user's own
  framing ("this feature is for mobile phone"). Desktop gets the same button and the same flow;
  it is just not the primary target for tuning the live-detection thresholds.
- **Multi-shot batch capture** (scan several documents in one sitting without leaving camera
  mode each time) — not requested, not built. `preprocess.py`'s own multi-document split already
  handles "two receipts caught in one photo"; this is a different feature (many separate photos
  in one session) that can be considered later if requested.

## 4. UX flow, end to end

```
Scanner page
  ↓ tap "Camera"
Live camera view opens (getUserMedia, facingMode: "environment" preferred on mobile)
  ↓ OpenCV.js lazy-loads in the background
Live bounding-box overlay tracks the detected document as the user frames the shot
  ↓ tap capture (shutter button)
Client-side quality gate runs (blur + framing)
  ├─ fails → plain-English reason shown, back to live view, nothing uploaded
  └─ passes ↓
Confirm screen: small preview + Category + Payment Method + [Retake] [Upload]
  ↓ tap Upload
scanInvoice(file) — identical to a browsed file from this point on
  ↓
updateInvoice(id, { category, payment_method })
  ↓
Existing scan-success/needs-review toast, existing paginated table, existing thumbnail/preview
```

## 5. Non-goals / explicit boundaries

- No new backend service, route, or database column. Category and payment method already exist
  on `Invoice` and are already settable via `PUT /invoices/{id}`.
- No change to `ocr/preprocess.py`, `extract_documents_with_engine`, or any part of the existing
  extraction pipeline — the whole point is that a camera-sourced image is indistinguishable from
  a browsed one by the time it reaches that code.
- No AI/ML model for the live overlay or blur check — both are classical, deterministic computer
  vision (contour detection, Laplacian variance), matching this project's own "rules-based, not
  a black box" discipline for everything else in the extraction pipeline.

## 6. Open question for you before implementation starts

Confirm or override: **ship Phase 1 without a manual crop/corner-adjustment editor** (§3.5),
relying on auto-detect + retake, and revisit only if real usage shows it's needed? This is the
one place in this design where the user's own stated wish ("can crop etc") and the
recommended-minimal-scope answer diverge, so it's called out rather than decided silently either
way.
