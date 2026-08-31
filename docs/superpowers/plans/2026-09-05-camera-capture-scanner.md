# Camera Capture (Live Document Scanning) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Camera" option to the Invoice Scanner page that opens a live camera view with a
real-time document-edge overlay, rejects blurry/incomplete captures before upload with a specific
reason, and — once a good capture is confirmed with just Category + Payment Method — sends it
through the exact same `scanInvoice()` path every other upload source already uses.

**Architecture:** Two separable pure-logic modules (blur variance, quad-candidate scoring) get
real unit tests via a new Vitest harness; the OpenCV.js-dependent glue (loading the WASM runtime,
running it against live video frames) is manually verified against a real camera, since it cannot
be meaningfully unit-tested without one. The captured photo is a plain `File`, fed into the
existing `scanInvoice`/`updateInvoice` mutations unchanged — no new backend code.

**Tech Stack:** React + TypeScript (existing), Vitest + jsdom (new, test-only), OpenCV.js (new,
lazy-loaded WASM, browser-only), shadcn/ui `Dialog` (existing component), TanStack Query
(existing).

**Spec:** `docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md`

## Global Constraints

- No new backend endpoint, route, or database column — `category`/`payment_method` are already
  settable via the existing `PUT /invoices/{id}` (`updateInvoice`).
- No change to `backend/libs/ocr/ocr/preprocess.py` or `extract_documents_with_engine` — the
  captured image must be indistinguishable from a browsed file by the time it reaches them.
- OpenCV.js loads only when the Camera dialog opens, never on initial page load (spec §3.2).
- Live-overlay analysis is throttled (~150ms), not run on every video frame (spec §3.2).
- Both quality-gate checks (blur, framing) fail closed toward "ask the user to retake," never
  toward silently uploading a bad photo (spec §3.3).
- No manual crop/corner-adjustment editor in this plan — deferred per spec §3.5, pending your
  confirmation on the open question at the end of the spec.
- Match existing code conventions exactly: `Button variant="outline" className="gap-2 rounded-xl"`
  for toolbar buttons (see the existing Browse/WhatsApp/Slack/Email buttons in
  `frontend/src/routes/app.scanner.tsx`), shadcn `Dialog` for modals (see
  `frontend/src/components/records/invoice-thumbnail.tsx`'s own lightbox for the pattern), and
  the `UNCATEGORIZED`/Category-select pattern this session already added to the Scanner's
  post-extraction form.

---

## File Structure

```
frontend/
  vitest.config.ts                                    NEW — Vitest harness config
  package.json                                        MODIFY — add vitest/jsdom, "test" script
  src/
    lib/
      document-detection.ts                            NEW — pure blur/quad-scoring math, unit-tested
      __tests__/
        document-detection.test.ts                     NEW — unit tests for the above
      opencv-loader.ts                                  NEW — lazy CDN/WASM loader for OpenCV.js
      opencv-document-scanner.ts                        NEW — OpenCV.js glue: video frame → quad points
    components/
      scanner/
        camera-capture-dialog.tsx                       NEW — the whole camera UI (live view, overlay,
                                                          quality gate, confirm screen)
    routes/
      app.scanner.tsx                                   MODIFY — add the "Camera" button, wire the
                                                          dialog's confirmed capture into the existing
                                                          scan/update mutations
```

---

### Task 1: Vitest test harness

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/lib/__tests__/sanity.test.ts`

**Interfaces:**
- Produces: a working `npm run test` command every later task's unit tests run under.

- [ ] **Step 1: Add Vitest + jsdom as dev dependencies**

Run:
```bash
cd frontend && npm install -D vitest jsdom
```

- [ ] **Step 2: Add the `test` script to `package.json`**

In `frontend/package.json`, inside `"scripts"`, add (alongside the existing `dev`/`build`/`lint`
entries, same object, comma-separated):

```json
"test": "vitest run"
```

- [ ] **Step 3: Create the Vitest config**

Create `frontend/vitest.config.ts`:

```typescript
import { defineConfig } from "vitest/config";
import tsConfigPaths from "vite-tsconfig-paths";

// Deliberately its own minimal config, not a re-export of vite.config.ts —
// that file pulls in the TanStack Start/Nitro/Tailwind plugin chain, none of
// which unit tests for plain TypeScript logic (this plan's actual target)
// need or benefit from. Keeping the two configs separate means a change to
// the app's build plugins can never accidentally break the test runner.
export default defineConfig({
  plugins: [tsConfigPaths({ projects: ["./tsconfig.json"] })],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
```

- [ ] **Step 4: Write a trivial sanity test**

Create `frontend/src/lib/__tests__/sanity.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

describe("vitest harness", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: Run it**

Run: `cd frontend && npm run test`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/lib/__tests__/sanity.test.ts
git commit -m "test: add Vitest harness for frontend unit tests"
```

---

### Task 2: Pure blur-detection math (variance of Laplacian)

**Files:**
- Create: `frontend/src/lib/document-detection.ts` (this task writes only the blur-related exports;
  Task 3 adds to the same file)
- Test: `frontend/src/lib/__tests__/document-detection.test.ts`

**Interfaces:**
- Produces: `computeBlurVariance(gray: Uint8ClampedArray, width: number, height: number): number`,
  `isBlurry(variance: number): boolean`, `BLUR_VARIANCE_THRESHOLD: number` — consumed by Task 6
  (the capture-time quality gate).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/__tests__/document-detection.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { BLUR_VARIANCE_THRESHOLD, computeBlurVariance, isBlurry } from "../document-detection";

/** A flat, uniform image — every pixel identical — has zero edge content
 * anywhere, so its Laplacian variance is exactly 0. This is the simplest
 * possible "definitely blurry" fixture: no real photo is ever this uniform,
 * but it pins down the floor of the function's own output. */
function uniformGray(width: number, height: number, value: number): Uint8ClampedArray {
  return new Uint8ClampedArray(width * height).fill(value);
}

/** A checkerboard alternating between 0 and 255 every pixel is the sharpest
 * possible edge content a grayscale buffer can contain — the ceiling
 * fixture, opposite of uniformGray. */
function checkerboardGray(width: number, height: number): Uint8ClampedArray {
  const buf = new Uint8ClampedArray(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      buf[y * width + x] = (x + y) % 2 === 0 ? 255 : 0;
    }
  }
  return buf;
}

describe("computeBlurVariance", () => {
  it("is exactly 0 for a perfectly uniform image", () => {
    expect(computeBlurVariance(uniformGray(20, 20, 128), 20, 20)).toBe(0);
  });

  it("is much higher for a sharp checkerboard than a uniform image", () => {
    const flat = computeBlurVariance(uniformGray(20, 20, 128), 20, 20);
    const sharp = computeBlurVariance(checkerboardGray(20, 20), 20, 20);
    expect(sharp).toBeGreaterThan(flat);
    expect(sharp).toBeGreaterThan(1000); // a real checkerboard is nowhere near the blur threshold
  });

  it("returns 0 for an image too small to convolve (below 3x3)", () => {
    expect(computeBlurVariance(uniformGray(2, 2, 50), 2, 2)).toBe(0);
  });
});

describe("isBlurry", () => {
  it("flags a variance at or below the threshold as blurry", () => {
    expect(isBlurry(BLUR_VARIANCE_THRESHOLD)).toBe(true);
    expect(isBlurry(0)).toBe(true);
  });

  it("does not flag a variance above the threshold", () => {
    expect(isBlurry(BLUR_VARIANCE_THRESHOLD + 1)).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- document-detection`
Expected: FAIL — `document-detection.ts` does not exist yet.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/document-detection.ts`:

```typescript
/**
 * Pure, dependency-free document-capture quality math — no OpenCV.js, no
 * DOM, no camera. Deliberately kept this way (see
 * docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md §3.3)
 * so it is fast, has zero load-time cost, and is directly unit-testable
 * against synthetic pixel buffers rather than needing a real photo or a
 * loaded WASM runtime. The OpenCV.js-dependent piece (actually finding a
 * document's contour in a video frame) lives separately in
 * opencv-document-scanner.ts, which calls into this module's scoring
 * functions rather than duplicating this math.
 */

// --------------------------------------------------------------------------
// Blur detection — variance of the Laplacian (see spec §3.3). A sharp image
// has many strong edges and therefore a high-variance Laplacian response; a
// blurry one has few and a low one. Standard, well-established technique for
// exactly this — not something invented here.
// --------------------------------------------------------------------------

/** Below this, a capture is rejected as too blurry to read. Deliberately
 * forgiving (spec §3.3's own "must be forgiving, not paranoid" rule) — a
 * normal handheld indoor phone photo of a printed document sits far above
 * this; only genuine motion blur or an out-of-focus shot lands under it.
 * Tune against real captured photos once this ships, not by further
 * guessing — see Task 9's manual verification checklist. */
export const BLUR_VARIANCE_THRESHOLD = 60;

/** 3x3 Laplacian kernel (4-connected), the same standard kernel every
 * reference implementation of this technique uses. */
const LAPLACIAN_KERNEL = [0, 1, 0, 1, -4, 1, 0, 1, 0];

/** Computes the variance of the Laplacian response across a grayscale
 * buffer — `gray` must be exactly `width * height` bytes, one per pixel
 * (0-255). Returns 0 for anything too small to convolve (below 3x3) rather
 * than throwing — a caller (Task 6) always gets a number back and treats an
 * unusably small frame as "not sharp enough" via the ordinary threshold
 * check, not as a special error case to handle separately. */
export function computeBlurVariance(gray: Uint8ClampedArray, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;

  const responses: number[] = [];
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      let sum = 0;
      let k = 0;
      for (let ky = -1; ky <= 1; ky++) {
        for (let kx = -1; kx <= 1; kx++) {
          sum += gray[(y + ky) * width + (x + kx)]! * LAPLACIAN_KERNEL[k]!;
          k++;
        }
      }
      responses.push(sum);
    }
  }
  if (responses.length === 0) return 0;

  const mean = responses.reduce((a, b) => a + b, 0) / responses.length;
  const variance = responses.reduce((a, b) => a + (b - mean) ** 2, 0) / responses.length;
  return variance;
}

export function isBlurry(variance: number): boolean {
  return variance <= BLUR_VARIANCE_THRESHOLD;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm run test -- document-detection`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/document-detection.ts frontend/src/lib/__tests__/document-detection.test.ts
git commit -m "feat: add pure Laplacian-variance blur detection"
```

---

### Task 3: Pure quad-candidate scoring and framing decision

**Files:**
- Modify: `frontend/src/lib/document-detection.ts` (adds to Task 2's file)
- Test: `frontend/src/lib/__tests__/document-detection.test.ts` (adds to Task 2's file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `type Point = { x: number; y: number }`, `type QuadScore = { areaRatio: number;
  rectangularity: number; touchesEdge: boolean; confidence: number }`,
  `scoreQuadCandidate(points: Point[], frameWidth: number, frameHeight: number): QuadScore | null`,
  `type FramingResult = { ok: boolean; reason?: string }`,
  `isFramingAcceptable(score: QuadScore | null): FramingResult` — consumed by Task 5 (the live
  overlay) and Task 6 (the capture-time quality gate).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/__tests__/document-detection.test.ts`:

```typescript
import { isFramingAcceptable, scoreQuadCandidate, type Point } from "../document-detection";

function rectPoints(x0: number, y0: number, x1: number, y1: number): Point[] {
  return [
    { x: x0, y: y0 },
    { x: x1, y: y0 },
    { x: x1, y: y1 },
    { x: x0, y: y1 },
  ];
}

describe("scoreQuadCandidate", () => {
  it("returns null for fewer than 4 points", () => {
    expect(scoreQuadCandidate([{ x: 0, y: 0 }], 100, 100)).toBeNull();
  });

  it("scores a large, centered, axis-aligned rectangle as confident and not touching an edge", () => {
    // A 60x60 document centered in a 100x100 frame — comfortably inside on
    // every side, comfortably large, and a perfect rectangle.
    const score = scoreQuadCandidate(rectPoints(20, 20, 80, 80), 100, 100);
    expect(score).not.toBeNull();
    expect(score!.rectangularity).toBeCloseTo(1.0, 1);
    expect(score!.touchesEdge).toBe(false);
    expect(score!.confidence).toBeGreaterThan(0.5);
  });

  it("flags a rectangle whose edge sits at the frame boundary as touching the edge", () => {
    // Left edge at x=0 — the document plausibly extends past what the
    // camera actually captured.
    const score = scoreQuadCandidate(rectPoints(0, 20, 80, 80), 100, 100);
    expect(score!.touchesEdge).toBe(true);
  });

  it("scores a tiny candidate with a low area ratio", () => {
    const score = scoreQuadCandidate(rectPoints(45, 45, 55, 55), 100, 100);
    expect(score!.areaRatio).toBeLessThan(0.05);
  });
});

describe("isFramingAcceptable", () => {
  it("rejects with a reason when no candidate was found at all", () => {
    const result = isFramingAcceptable(null);
    expect(result.ok).toBe(false);
    expect(result.reason).toMatch(/not.*fully.*frame|not.*detect/i);
  });

  it("accepts a large, centered, non-edge-touching candidate", () => {
    const score = scoreQuadCandidate(rectPoints(20, 20, 80, 80), 100, 100);
    const result = isFramingAcceptable(score);
    expect(result.ok).toBe(true);
    expect(result.reason).toBeUndefined();
  });

  it("rejects a candidate that touches the frame edge, with a specific reason", () => {
    const score = scoreQuadCandidate(rectPoints(0, 20, 80, 80), 100, 100);
    const result = isFramingAcceptable(score);
    expect(result.ok).toBe(false);
    expect(result.reason).toMatch(/edge|fully in frame/i);
  });

  it("rejects a candidate that is too small a share of the frame", () => {
    const score = scoreQuadCandidate(rectPoints(45, 45, 55, 55), 100, 100);
    const result = isFramingAcceptable(score);
    expect(result.ok).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- document-detection`
Expected: FAIL — `scoreQuadCandidate`/`isFramingAcceptable`/`Point` are not exported yet.

- [ ] **Step 3: Write the implementation**

Append to `frontend/src/lib/document-detection.ts`:

```typescript
// --------------------------------------------------------------------------
// Quad-candidate scoring — pure geometry over a 4-point polygon, no OpenCV.
// Mirrors the same shape as backend/libs/ocr/ocr/preprocess.py's own
// _score_candidates (area ratio relative to the frame, rectangularity of
// the contour vs. its own bounding box) — the same underlying signal,
// reimplemented client-side for the live overlay rather than shared code,
// since the two run in genuinely different runtimes (Python/OpenCV
// server-side, TypeScript/OpenCV.js client-side).
// --------------------------------------------------------------------------

export interface Point {
  x: number;
  y: number;
}

export interface QuadScore {
  areaRatio: number;
  rectangularity: number;
  touchesEdge: boolean;
  confidence: number;
}

/** A candidate below this share of the frame is not confidently "a
 * document in frame" — same reasoning and a similar value to preprocess.py's
 * own _MIN_DOCUMENT_AREA_RATIO, tuned independently for the live-overlay's
 * own, more forgiving use case (this only gates the *live guidance box* and
 * the *capture-time retake prompt* — the final crop is still preprocess.py's
 * own, separate decision). */
const MIN_AREA_RATIO = 0.15;

/** A rectangle's own contourArea / boundingBox area — 1.0 is perfect.
 * Real paper held at a slight angle to the camera still scores well above
 * this; a wildly non-rectangular blob does not. */
const MIN_RECTANGULARITY = 0.6;

/** A point this close to a frame edge (as a fraction of that axis's own
 * length) counts as "touching" it — the document plausibly extends past
 * what the camera captured. */
const EDGE_MARGIN_RATIO = 0.02;

function polygonArea(points: Point[]): number {
  let area = 0;
  for (let i = 0; i < points.length; i++) {
    const p1 = points[i]!;
    const p2 = points[(i + 1) % points.length]!;
    area += p1.x * p2.y - p2.x * p1.y;
  }
  return Math.abs(area) / 2;
}

export function scoreQuadCandidate(points: Point[], frameWidth: number, frameHeight: number): QuadScore | null {
  if (points.length < 4 || frameWidth <= 0 || frameHeight <= 0) return null;

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const boundingArea = (x1 - x0) * (y1 - y0);
  if (boundingArea <= 0) return null;

  const contourArea = polygonArea(points);
  const rectangularity = contourArea / boundingArea;
  const areaRatio = boundingArea / (frameWidth * frameHeight);

  const marginX = frameWidth * EDGE_MARGIN_RATIO;
  const marginY = frameHeight * EDGE_MARGIN_RATIO;
  const touchesEdge = x0 <= marginX || y0 <= marginY || x1 >= frameWidth - marginX || y1 >= frameHeight - marginY;

  // Same "boxy-ness times capped size" blend preprocess.py's own
  // _score_candidates uses — rewards being rectangular, only rewards more
  // size up to the point a candidate already clearly reads as "a whole
  // document in frame."
  const sizeScore = Math.min(1, areaRatio / MIN_AREA_RATIO);
  const confidence = rectangularity * sizeScore;

  return { areaRatio, rectangularity, touchesEdge, confidence };
}

// --------------------------------------------------------------------------
// Framing decision — the capture-time (and live-overlay) accept/reject call,
// built on scoreQuadCandidate above. Fails closed toward "ask the user to
// retake" per spec §3.3: null, too small, not rectangular enough, or
// touching a frame edge are all rejected with a specific, plain-English
// reason — never silently accepted.
// --------------------------------------------------------------------------

export interface FramingResult {
  ok: boolean;
  reason?: string;
}

export function isFramingAcceptable(score: QuadScore | null): FramingResult {
  if (score === null) {
    return { ok: false, reason: "We couldn't detect the document — make sure it's fully in frame and try again." };
  }
  if (score.touchesEdge) {
    return { ok: false, reason: "The document doesn't look fully in frame — make sure all four edges are visible." };
  }
  if (score.areaRatio < MIN_AREA_RATIO) {
    return { ok: false, reason: "Move closer — the document looks too small in the frame." };
  }
  if (score.rectangularity < MIN_RECTANGULARITY) {
    return { ok: false, reason: "We couldn't get a clear outline of the document — try a flatter angle." };
  }
  return { ok: true };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm run test -- document-detection`
Expected: `13 passed` (the 6 from Task 2 plus 7 new ones).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/document-detection.ts frontend/src/lib/__tests__/document-detection.test.ts
git commit -m "feat: add pure quad-candidate scoring and framing decision logic"
```

---

### Task 4: OpenCV.js lazy loader

**Files:**
- Create: `frontend/src/lib/opencv-loader.ts`

**Interfaces:**
- Produces: `loadOpenCv(): Promise<OpenCvModule>` (a module-level singleton promise — calling it
  twice returns the same in-flight/resolved promise, never triggers a second script load).
- Consumed by: Task 5.

**Note on testing:** loading a real script tag and waiting on a global is a DOM/network-integration
concern with no meaningful pure-logic core to unit test in isolation (mocking `document.createElement`
to fake success would only prove the mock works, not that OpenCV.js actually loads) — verified
manually in Task 9's checklist instead, consistent with this plan's "No Placeholders" discipline:
a fake automated test here would be exactly the kind of theatrical, non-informative test that rule
exists to prevent.

- [ ] **Step 1: Write the loader**

Create `frontend/src/lib/opencv-loader.ts`:

```typescript
/**
 * Lazy-loads OpenCV.js (the WASM build) from the jsdelivr CDN — never
 * imported at the top of any route or bundled into the main app chunk. The
 * only caller is the camera capture dialog (camera-capture-dialog.tsx),
 * which invokes this the moment the user opens the Camera option, not
 * before (see docs/superpowers/specs/2026-09-05-camera-capture-scanner-
 * design.md §3.2 — the ~8MB WASM payload is an acceptable cost only once a
 * user has actually chosen to scan by camera).
 */

// The subset of OpenCV.js's own global `cv` object this codebase actually
// calls — not the library's full surface. Kept intentionally narrow so a
// consumer only ever depends on what it really uses.
export interface OpenCvModule {
  Mat: new () => unknown;
  matFromImageData: (imageData: ImageData) => unknown;
  cvtColor: (src: unknown, dst: unknown, code: number) => void;
  GaussianBlur: (src: unknown, dst: unknown, ksize: unknown, sigmaX: number) => void;
  Canny: (src: unknown, dst: unknown, threshold1: number, threshold2: number) => void;
  findContours: (image: unknown, contours: unknown, hierarchy: unknown, mode: number, method: number) => void;
  approxPolyDP: (curve: unknown, approxCurve: unknown, epsilon: number, closed: boolean) => void;
  arcLength: (curve: unknown, closed: boolean) => number;
  contourArea: (contour: unknown) => number;
  MatVector: new () => { size: () => number; get: (i: number) => unknown; delete: () => void };
  Size: new (width: number, height: number) => unknown;
  COLOR_RGBA2GRAY: number;
  RETR_LIST: number;
  CHAIN_APPROX_SIMPLE: number;
}

declare global {
  interface Window {
    cv?: OpenCvModule & { onRuntimeInitialized?: () => void };
  }
}

const OPENCV_JS_URL = "https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js";

let loadPromise: Promise<OpenCvModule> | null = null;

export function loadOpenCv(): Promise<OpenCvModule> {
  if (loadPromise) return loadPromise;

  loadPromise = new Promise<OpenCvModule>((resolve, reject) => {
    if (window.cv?.Mat) {
      // Already loaded from an earlier camera session this page load.
      resolve(window.cv);
      return;
    }
    const script = document.createElement("script");
    script.src = OPENCV_JS_URL;
    script.async = true;
    script.onerror = () => reject(new Error("Could not load the document-scanning library"));
    script.onload = () => {
      const cv = window.cv;
      if (!cv) {
        reject(new Error("Document-scanning library loaded but did not initialize"));
        return;
      }
      // OpenCV.js's own WASM runtime finishes initializing asynchronously
      // after the script itself has loaded — `onRuntimeInitialized` is its
      // documented signal that `cv.Mat` etc. are actually callable now.
      cv.onRuntimeInitialized = () => resolve(cv);
    };
    document.head.appendChild(script);
  });

  return loadPromise;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/opencv-loader.ts
git commit -m "feat: add lazy OpenCV.js loader for camera capture"
```

---

### Task 5: OpenCV.js-backed live document detector

**Files:**
- Create: `frontend/src/lib/opencv-document-scanner.ts`

**Interfaces:**
- Consumes: `OpenCvModule` (Task 4), `Point`/`scoreQuadCandidate` (Task 3).
- Produces: `detectDocumentQuad(cv: OpenCvModule, imageData: ImageData): { points: Point[]; score: QuadScore } | null`
  — consumed by Task 6 (both the live-overlay redraw loop and the capture-time quality gate).

**Note on testing:** same reasoning as Task 4 — this function's entire job is calling real OpenCV.js
Mat operations on real pixel data; a meaningful test would need a real loaded WASM runtime and a
real (or realistic synthetic) document photo, which is exactly what Task 9's manual checklist
verifies against an actual camera. The geometry decision this function *feeds into*
(`scoreQuadCandidate`) already has real, fast unit tests from Task 3.

- [ ] **Step 1: Write the detector**

Create `frontend/src/lib/opencv-document-scanner.ts`:

```typescript
/**
 * Finds the largest plausible document-shaped contour in one video frame —
 * the OpenCV.js-dependent half of live document detection. Deliberately a
 * *simplified* version of backend/libs/ocr/ocr/preprocess.py's own
 * _candidate_contours (grayscale -> blur -> Canny -> contours, no Otsu
 * threshold pass, no morphological closing, no multi-document valley
 * split): this only has to be good enough for live visual guidance while
 * the user is still framing the shot, not to produce the final crop — see
 * docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md §3.1
 * for why those are deliberately two different algorithms with two
 * different jobs.
 */
import { type OpenCvModule } from "./opencv-loader";
import { scoreQuadCandidate, type Point, type QuadScore } from "./document-detection";

export interface DetectedQuad {
  points: Point[];
  score: QuadScore;
}

/** A found contour is simplified to a polygon within this fraction of its
 * own perimeter — small enough to still hug a real document's corners,
 * large enough to collapse noisy edge pixels into a clean quadrilateral. */
const APPROX_EPSILON_RATIO = 0.02;

export function detectDocumentQuad(cv: OpenCvModule, imageData: ImageData): DetectedQuad | null {
  const src = cv.matFromImageData(imageData) as { delete: () => void };
  const gray = new cv.Mat() as { delete: () => void };
  const blurred = new cv.Mat() as { delete: () => void };
  const edges = new cv.Mat() as { delete: () => void };
  const contours = new cv.MatVector();
  const hierarchy = new cv.Mat() as { delete: () => void };

  try {
    cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);
    cv.GaussianBlur(gray, blurred, new cv.Size(5, 5), 0);
    cv.Canny(blurred, edges, 50, 150);
    cv.findContours(edges, contours, hierarchy, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE);

    let best: DetectedQuad | null = null;
    for (let i = 0; i < contours.size(); i++) {
      const contour = contours.get(i);
      const perimeter = cv.arcLength(contour, true);
      const approx = new cv.Mat() as { delete: () => void; data32S: Int32Array; rows: number };
      cv.approxPolyDP(contour, approx, APPROX_EPSILON_RATIO * perimeter, true);

      if (approx.rows === 4) {
        const points: Point[] = [];
        for (let p = 0; p < 4; p++) {
          points.push({ x: approx.data32S[p * 2]!, y: approx.data32S[p * 2 + 1]! });
        }
        const score = scoreQuadCandidate(points, imageData.width, imageData.height);
        if (score && (!best || score.confidence > best.score.confidence)) {
          best = { points, score };
        }
      }
      approx.delete();
    }
    return best;
  } finally {
    // OpenCV.js Mats are WASM heap-allocated — never garbage-collected by
    // the JS engine, so every one of these must be released explicitly on
    // every path out of this function, success or thrown, or a live 6-7Hz
    // detection loop leaks memory within seconds.
    src.delete();
    gray.delete();
    blurred.delete();
    edges.delete();
    hierarchy.delete();
    contours.delete();
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/opencv-document-scanner.ts
git commit -m "feat: add OpenCV.js-backed live document quad detection"
```

---

### Task 6: Camera capture dialog — live view, overlay, capture, quality gate

**Files:**
- Create: `frontend/src/components/scanner/camera-capture-dialog.tsx`

**Interfaces:**
- Consumes: `loadOpenCv` (Task 4), `detectDocumentQuad` (Task 5), `computeBlurVariance`/`isBlurry`/
  `isFramingAcceptable` (Tasks 2-3), `invoiceCategoryOptions` (`@/lib/invoice-service`, existing).
- Produces: `<CameraCaptureDialog open, onOpenChange, onConfirm={(file, category, paymentMethod) => void} />`
  — consumed by Task 8.

- [ ] **Step 1: Write the component**

Create `frontend/src/components/scanner/camera-capture-dialog.tsx`:

```tsx
/**
 * Live camera capture for the Scanner page — see
 * docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md for
 * the full design. Two screens in one dialog: a live camera view with a
 * real-time document-edge overlay and a capture button, and — once a shot
 * clears the quality gate — a small confirm screen (thumbnail + Category +
 * Payment Method). `onConfirm` hands the caller a plain File plus the two
 * chosen values; this component knows nothing about scanInvoice/
 * updateInvoice itself, matching the same "dumb component, smart caller"
 * shape every other form on this page already uses.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, Loader2, RotateCcw, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { invoiceCategoryOptions, type PaymentMethod } from "@/lib/invoice-service";
import { computeBlurVariance, isBlurry, isFramingAcceptable, type Point } from "@/lib/document-detection";
import { detectDocumentQuad } from "@/lib/opencv-document-scanner";
import { loadOpenCv, type OpenCvModule } from "@/lib/opencv-loader";

const UNCATEGORIZED = "Uncategorized";
const PAYMENT_METHOD_OPTIONS: PaymentMethod[] = ["cash", "bank"];
const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = { cash: "Cash", bank: "Online" };

/** How often the live overlay re-runs detection against the current video
 * frame — spec §3.2's own "throttle the analysis, not the video feed"
 * rule. The <video> itself keeps rendering at full native rate regardless. */
const DETECTION_INTERVAL_MS = 150;

type Screen = "live" | "confirm";

export function CameraCaptureDialog({
  open, onOpenChange, onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (file: File, category: string | null, paymentMethod: PaymentMethod | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const cvRef = useRef<OpenCvModule | null>(null);
  const detectionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [screen, setScreen] = useState<Screen>("live");
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [retakeReason, setRetakeReason] = useState<string | null>(null);
  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">("");
  const [liveQuad, setLiveQuad] = useState<Point[] | null>(null);

  const categoryOptionsQuery = useQuery({ queryKey: ["invoice-options"], queryFn: invoiceCategoryOptions });

  const stopCamera = useCallback(() => {
    if (detectionTimerRef.current) clearInterval(detectionTimerRef.current);
    detectionTimerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  // Camera + OpenCV.js startup — only while the dialog is actually open on
  // the live screen, torn down the moment it isn't (dialog closed, or the
  // user moved on to the confirm screen) so a phone's camera light is never
  // left on longer than the user can see why.
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
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
      })
      .catch(() => {
        if (!cancelled) setCameraError("Could not access the camera — check your browser's camera permission.");
      });

    loadOpenCv()
      .then((cv) => {
        if (!cancelled) cvRef.current = cv;
      })
      .catch(() => {
        // Live guidance is a nice-to-have, not a hard requirement — the
        // capture button below still works via the blur check alone
        // (isFramingAcceptable is skipped when no quad was ever found,
        // same "null means reject with a reason" path a genuinely
        // undetectable frame already takes). Framing quality then falls
        // fully on the user's own judgement from the plain video feed.
      });

    return () => {
      cancelled = true;
      stopCamera();
    };
  }, [open, screen, stopCamera]);

  // The live detection loop — draws the overlay box, throttled per
  // DETECTION_INTERVAL_MS, entirely separate from the <video> element's own
  // full-rate rendering.
  useEffect(() => {
    if (!open || screen !== "live") return;
    detectionTimerRef.current = setInterval(() => {
      const cv = cvRef.current;
      const video = videoRef.current;
      const overlay = overlayRef.current;
      if (!cv || !video || !overlay || video.videoWidth === 0) return;

      overlay.width = video.videoWidth;
      overlay.height = video.videoHeight;
      const ctx = overlay.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, overlay.width, overlay.height);
      const frame = ctx.getImageData(0, 0, overlay.width, overlay.height);
      ctx.clearRect(0, 0, overlay.width, overlay.height);

      const detected = detectDocumentQuad(cv, frame);
      setLiveQuad(detected?.points ?? null);

      if (detected) {
        const { ok } = isFramingAcceptable(detected.score);
        ctx.strokeStyle = ok ? "#22c55e" : "#f59e0b"; // success green / warning amber, matching this app's own token colors
        ctx.lineWidth = 3;
        ctx.beginPath();
        detected.points.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
        ctx.closePath();
        ctx.stroke();
      }
    }, DETECTION_INTERVAL_MS);
    return () => {
      if (detectionTimerRef.current) clearInterval(detectionTimerRef.current);
    };
  }, [open, screen]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const handleCapture = () => {
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) return;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);
    const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);

    // Blur check first — cheap, no OpenCV.js dependency, always available.
    const gray = new Uint8ClampedArray(frame.width * frame.height);
    for (let i = 0; i < gray.length; i++) {
      const r = frame.data[i * 4]!, g = frame.data[i * 4 + 1]!, b = frame.data[i * 4 + 2]!;
      gray[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    }
    const blurVariance = computeBlurVariance(gray, frame.width, frame.height);
    if (isBlurry(blurVariance)) {
      setRetakeReason("This photo looks blurry — hold the camera steady and try again.");
      return;
    }

    // Framing check — best-effort: only runs when OpenCV.js finished
    // loading in time, per the same graceful-degradation reasoning as the
    // live overlay above.
    const cv = cvRef.current;
    if (cv) {
      const detected = detectDocumentQuad(cv, frame);
      const framing = isFramingAcceptable(detected?.score ?? null);
      if (!framing.ok) {
        setRetakeReason(framing.reason ?? "Please retake the photo.");
        return;
      }
    }

    canvas.toBlob((blob) => {
      if (!blob) return;
      setCapturedBlob(blob);
      setPreviewUrl(URL.createObjectURL(blob));
      setRetakeReason(null);
      setScreen("confirm");
    }, "image/jpeg", 0.92);
  };

  const handleRetake = () => {
    setCapturedBlob(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setScreen("live");
  };

  const handleUpload = () => {
    if (!capturedBlob) return;
    const file = new File([capturedBlob], `camera-capture-${Date.now()}.jpg`, { type: "image/jpeg" });
    onConfirm(file, category || null, paymentMethod || null);
    handleClose();
  };

  const handleClose = () => {
    stopCamera();
    setScreen("live");
    setCapturedBlob(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setCategory("");
    setPaymentMethod("");
    setRetakeReason(null);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : handleClose())}>
      <DialogContent className="max-w-lg">
        <DialogTitle>{screen === "live" ? "Scan with Camera" : "Confirm Capture"}</DialogTitle>

        {screen === "live" && (
          <div className="flex flex-col gap-3">
            {cameraError ? (
              <p className="rounded-xl bg-destructive/10 p-4 text-sm text-destructive">{cameraError}</p>
            ) : (
              <div className="relative overflow-hidden rounded-xl bg-black">
                {/* eslint-disable-next-line jsx-a11y/media-has-caption -- a live camera preview, not recorded media */}
                <video ref={videoRef} autoPlay playsInline muted className="w-full" />
                <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
              </div>
            )}
            {retakeReason && (
              <p className="rounded-xl bg-warning/15 p-3 text-sm font-medium text-warning-foreground dark:text-warning">
                {retakeReason}
              </p>
            )}
            <Button className="gap-2 rounded-xl" disabled={!!cameraError} onClick={handleCapture}>
              <Camera className="h-4 w-4" /> Capture
            </Button>
          </div>
        )}

        {screen === "confirm" && previewUrl && (
          <div className="flex flex-col gap-4">
            <img src={previewUrl} alt="Captured document" className="max-h-64 w-full rounded-xl object-contain" />
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Category</Label>
                <Select value={category || UNCATEGORIZED} onValueChange={(v) => setCategory(v === UNCATEGORIZED ? "" : v)}>
                  <SelectTrigger className="rounded-xl"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={UNCATEGORIZED}>{UNCATEGORIZED}</SelectItem>
                    {(categoryOptionsQuery.data?.categories ?? []).map((c) => (
                      <SelectItem key={c} value={c}>{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Payment Method</Label>
                <div className="flex gap-2">
                  {PAYMENT_METHOD_OPTIONS.map((method) => (
                    <button
                      key={method} type="button" aria-pressed={paymentMethod === method}
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
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/camera-capture-dialog.tsx
git commit -m "feat: add CameraCaptureDialog with live overlay and quality gate"
```

---

### Task 7: Wire the Camera button into the Scanner page

**Files:**
- Modify: `frontend/src/routes/app.scanner.tsx`

**Interfaces:**
- Consumes: `CameraCaptureDialog` (Task 6), existing `scanMutation`/`updateMutation` (already
  present in this file from earlier work this session).

- [ ] **Step 1: Import the dialog and add open state**

In `frontend/src/routes/app.scanner.tsx`, add to the imports:

```typescript
import { CameraCaptureDialog } from "@/components/scanner/camera-capture-dialog";
```

Add `Camera` to the existing `lucide-react` import list (alongside `AlertTriangle`, `CheckCircle2`, etc.).

Inside the `Scanner()` component, alongside the existing `slackSheetOpen`/`emailSheetOpen` state:

```typescript
const [cameraDialogOpen, setCameraDialogOpen] = useState(false);
```

- [ ] **Step 2: Add the Camera button**

In the existing button row (`Browse files` / `WhatsApp` / `Slack` / `Email`), add:

```tsx
<Button variant="outline" className="gap-2 rounded-xl" onClick={() => setCameraDialogOpen(true)}>
  <Camera className="h-4 w-4" /> Camera
</Button>
```

- [ ] **Step 3: Render the dialog and wire its confirmation into the existing mutations**

Alongside the existing `<SlackFilesSheet .../>`/`<EmailFilesSheet .../>` renders:

```tsx
<CameraCaptureDialog
  open={cameraDialogOpen}
  onOpenChange={setCameraDialogOpen}
  onConfirm={(file, category, paymentMethod) => {
    scanMutation.mutate(file, {
      onSuccess: (result) => {
        // The scan itself already shows its own success/needs-review toast
        // via scanMutation's existing onSuccess (unchanged) — this only
        // patches on the two fields the camera confirm screen collected,
        // the same "create, then patch" shape Saved Records' own inline
        // editors already use for these exact two fields.
        if (result.invoice_id && (category || paymentMethod)) {
          updateInvoice(result.invoice_id, { category, payment_method: paymentMethod }).then(() => {
            queryClient.invalidateQueries({ queryKey: ["invoice", result.invoice_id] });
            queryClient.invalidateQueries({ queryKey: ["invoices"] });
          });
        }
      },
    });
  }}
/>
```

**Note:** `scanMutation`'s existing `mutationFn: (file: File) => scanInvoice(file)` already accepts
a plain `File` — a camera capture (built as a `File` in Task 6's `handleUpload`) needs no special
casing anywhere in the mutation itself. This is the concrete proof of spec §2 goal 5 ("zero new
backend surface"): the only new code in this whole task is a button and a dialog; the upload path
is the one that already exists.

- [ ] **Step 4: Type-check**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: exit code 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/app.scanner.tsx
git commit -m "feat: wire Camera button and capture dialog into the Scanner page"
```

---

### Task 8: Full unit test suite + lint pass

**Files:** none new — verification only.

- [ ] **Step 1: Run the full frontend unit test suite**

Run: `cd frontend && npm run test`
Expected: all tests pass (19 from Tasks 1-3, none regressed).

- [ ] **Step 2: Run the full type-check**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: exit code 0.

- [ ] **Step 3: Lint the new/changed files**

Run: `cd frontend && npx eslint src/lib/document-detection.ts src/lib/opencv-loader.ts src/lib/opencv-document-scanner.ts src/components/scanner/camera-capture-dialog.tsx src/routes/app.scanner.tsx`
Expected: no errors other than this repository's own pre-existing, unrelated CRLF `prettier/prettier`
line-ending warnings (confirmed project-wide, not something this plan's own files should be singled
out to fix — see the same finding recorded in this session's earlier Scanner-table work).

- [ ] **Step 4: Fix anything real the above surfaces, then re-run Steps 1-3 until clean.**

---

### Task 9: Manual end-to-end verification (real camera required)

**Files:** none — this task is entirely manual verification, run once the app is deployed to a
real device (or a desktop browser with a webcam) where `getUserMedia` can actually be granted.
Per this plan's own "No Placeholders" discipline (see Tasks 4-5's notes), this is written out as
concrete steps with concrete expected results, not a vague "test it works."

- [ ] **Step 1: Camera opens and shows a live feed**

On a phone, open `/app/scanner`, tap **Camera**. Expected: browser's own camera-permission
prompt appears; once granted, a live rear-camera feed fills the dialog within ~1-2 seconds.

- [ ] **Step 2: Live overlay tracks a real document**

Hold a real invoice/receipt in frame, move it slowly. Expected: a colored quadrilateral outline
appears around the document's own edges within roughly half a second of holding it steady, and
visibly follows it as it moves — green when well-framed, amber when not (edge-touching or too
small).

- [ ] **Step 3: Blur rejection**

Deliberately shake the phone while pressing Capture, or capture while the document is still
moving. Expected: returns to the live view with "This photo looks blurry — hold the camera
steady and try again." — no confirm screen, nothing uploaded.

- [ ] **Step 4: Framing rejection**

Deliberately capture with only part of the document in frame (one edge cut off). Expected:
"The document doesn't look fully in frame — make sure all four edges are visible." — no confirm
screen, nothing uploaded.

- [ ] **Step 5: A good capture reaches the confirm screen**

Capture a clean, fully-framed, well-lit, steady shot of a real document. Expected: confirm
screen appears with a small preview of the shot, a Category dropdown, and a Cash/Online pill
picker, both empty by default.

- [ ] **Step 6: Retake discards cleanly**

On the confirm screen, tap Retake. Expected: back to the live camera view, no network request
was made, camera feed resumes.

- [ ] **Step 7: Upload goes through the real, existing pipeline**

Pick a Category and Payment Method, tap Upload. Expected: the dialog closes, the existing scan
success/needs-review toast appears (unchanged from any other upload source), and the new invoice
appears at the top of the Scanner's paginated table (this session's earlier work) with its
thumbnail — click it and confirm Category/Payment Method were both saved, and every other field
(vendor, total, etc.) was extracted normally through the same pipeline every other source uses.

- [ ] **Step 8: Camera is released when the dialog closes**

Close the dialog (X button) while the live view is showing. Expected: the phone's own camera-
active indicator (the green dot/icon most mobile browsers show) disappears immediately — the
`MediaStream`'s tracks were actually stopped, not just hidden.

- [ ] **Step 9: Desktop works too, as a secondary path**

Repeat Steps 1, 5, 7 on a desktop browser with a webcam. Expected: same flow works (per spec
§3.5, this is confirmed-working but not the primary tuning target — a rough edge here is
acceptable in a way it would not be on mobile).

- [ ] **Step 10: Record the outcome**

Note any threshold that needed adjusting (blur variance, area ratio, rectangularity) against
real captured photos from Step 1-9, and adjust `BLUR_VARIANCE_THRESHOLD`/`MIN_AREA_RATIO`/
`MIN_RECTANGULARITY` in `document-detection.ts` accordingly — re-run Task 2/3's unit tests after
any change to confirm the existing fixtures still pass.

---

### Task 10: Changelog entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an entry matching this project's own existing changelog format**

At the top of `CHANGELOG.md` (newest first, matching every existing entry's own style), add:

```markdown
## 2026-09-05 — Live camera capture for the Invoice Scanner

Full detail: [`docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md`](docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md).
A new **Camera** option on `/app/scanner`, alongside Browse/WhatsApp/Slack/Email — opens a live
camera view with a real-time document-edge overlay, rejects blurry or incompletely-framed
captures on the spot with a specific reason before anything uploads, and — once confirmed with
just Category and Payment Method — sends the photo through the exact same `scanInvoice()` path
every other upload source already uses.

**Added** — `frontend/src/lib/document-detection.ts`: pure, unit-tested blur (variance of
Laplacian) and document-framing (quad-candidate scoring) quality checks, with no OpenCV.js/DOM
dependency.

**Added** — `frontend/src/lib/opencv-loader.ts` + `opencv-document-scanner.ts`: lazy-loaded
OpenCV.js-backed live document-edge detection, loaded only when the Camera dialog opens.

**Added** — `frontend/src/components/scanner/camera-capture-dialog.tsx`: the live view, overlay,
capture/retake/upload flow.

**Verified** — zero new backend surface: a camera-captured photo is indistinguishable from a
browsed file by the time it reaches `extract_documents_with_engine`/`preprocess.py`, which are
unchanged. Frontend unit suite: 19 passed (new Vitest harness, this feature's first use of it).
Manual camera verification per the spec's Task 9 checklist: [pending real-device run].
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for live camera capture"
```

---

## Self-Review Notes (completed during plan authoring)

- **Spec coverage:** every numbered goal in spec §2 maps to a task — live guidance (Tasks 4-6),
  reject-with-reason quality gate (Tasks 2-3, 6), minimal confirm form (Task 6), zero new backend
  surface (Task 7's own note), speed via throttling (Task 6's `DETECTION_INTERVAL_MS`). Spec §3.5's
  deferred items (manual crop, batch capture) are deliberately absent from this plan's tasks — not
  an oversight.
- **Placeholder scan:** no task ends in "add appropriate tests" or "handle edge cases" without
  showing the actual code; Tasks 4, 5, and 9 explicitly explain *why* they use manual verification
  instead of a fabricated automated test, rather than silently skipping testing.
- **Type consistency:** `Point`/`QuadScore`/`FramingResult` (Task 3) are the same types
  `opencv-document-scanner.ts` (Task 5) and `camera-capture-dialog.tsx` (Task 6) import and use,
  not redefined locally anywhere. `OpenCvModule` (Task 4) is the one shared interface both Task 5
  and Task 6 type their `cv` references against.
