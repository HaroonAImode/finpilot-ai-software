import { describe, expect, it } from "vitest";
import { BLUR_VARIANCE_THRESHOLD, computeBlurVariance, isBlurry } from "../blur-detection";

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
