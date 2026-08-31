/**
 * Pure, dependency-free blur detection — variance of the Laplacian. A
 * sharp image has many strong edges and therefore a high-variance
 * Laplacian response; a blurry one has few and a low one. Standard,
 * well-established technique for exactly this, not something invented
 * here. Kept as its own small, focused module (split out from the earlier,
 * larger document-detection.ts, which also carried document-boundary/
 * framing logic that proved unreliable in the field and was removed —
 * this piece was never the problem and is kept on its own).
 */

/** Below this, a capture is rejected as too blurry to read. Deliberately
 * forgiving — a normal handheld indoor phone photo of a printed document
 * sits far above this; only genuine motion blur or an out-of-focus shot
 * lands under it. */
export const BLUR_VARIANCE_THRESHOLD = 60;

/** 3x3 Laplacian kernel (4-connected), the same standard kernel every
 * reference implementation of this technique uses. */
const LAPLACIAN_KERNEL = [0, 1, 0, 1, -4, 1, 0, 1, 0];

/** Computes the variance of the Laplacian response across a grayscale
 * buffer — `gray` must be exactly `width * height` bytes, one per pixel
 * (0-255). Returns 0 for anything too small to convolve (below 3x3) rather
 * than throwing — a caller always gets a number back and treats an
 * unusably small frame as "not sharp enough" via the ordinary threshold
 * check, not as a special error case to handle separately. */
export function computeBlurVariance(
  gray: Uint8ClampedArray,
  width: number,
  height: number,
): number {
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
