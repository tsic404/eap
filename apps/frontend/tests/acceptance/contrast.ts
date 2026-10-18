/**
 * Contrast arithmetic per C-84: sRGB components are normalized, linearized,
 * weighted into relative luminance, and compared with the unrounded ratio.
 * Translucent layers are composited onto their parent before comparison.
 */

export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

const HEX_PATTERN = /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const RGB_PATTERN =
  /^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)$/i;

/** Parses the hex and `rgb()` forms the stylesheet and Tailwind classes use. */
export function parseColor(value: string): Rgba {
  const text = value.trim();
  if (HEX_PATTERN.test(text)) {
    let digits = text.slice(1);
    if (digits.length === 3) {
      digits = [...digits].map((digit) => digit + digit).join("");
    }
    const channel = (offset: number) =>
      parseInt(digits.slice(offset, offset + 2), 16) / 255;
    return {
      r: channel(0),
      g: channel(2),
      b: channel(4),
      a: digits.length === 8 ? parseInt(digits.slice(6, 8), 16) / 255 : 1,
    };
  }
  const rgb = text.match(RGB_PATTERN);
  if (rgb) {
    return {
      r: Number(rgb[1]) / 255,
      g: Number(rgb[2]) / 255,
      b: Number(rgb[3]) / 255,
      a: rgb[4] === undefined ? 1 : Number(rgb[4]),
    };
  }
  throw new Error(`unsupported colour value: ${value}`);
}

/** C-84 linearization and the luminance weights for normalized sRGB. */
export function relativeLuminance(color: Rgba): number {
  const linear = (channel: number) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  return (
    0.2126 * linear(color.r) +
    0.7152 * linear(color.g) +
    0.0722 * linear(color.b)
  );
}

/** Source-over compositing of a translucent foreground onto an opaque layer. */
export function composite(foreground: Rgba, background: Rgba): Rgba {
  const mix = (top: number, bottom: number) =>
    top * foreground.a + bottom * (1 - foreground.a);
  return {
    r: mix(foreground.r, background.r),
    g: mix(foreground.g, background.g),
    b: mix(foreground.b, background.b),
    a: 1,
  };
}

/**
 * Contrast ratio between a foreground and a background colour. A translucent
 * background is composited onto `parent` first, as C-84 requires.
 */
export function contrastRatio(
  foreground: string,
  background: string,
  parent = "#ffffff",
): number {
  let backgroundValue = parseColor(background);
  if (backgroundValue.a < 1) {
    backgroundValue = composite(backgroundValue, parseColor(parent));
  }
  let foregroundValue = parseColor(foreground);
  if (foregroundValue.a < 1) {
    foregroundValue = composite(foregroundValue, backgroundValue);
  }
  const [lighter, darker] = [
    relativeLuminance(backgroundValue),
    relativeLuminance(foregroundValue),
  ].sort((left, right) => right - left);
  return (lighter + 0.05) / (darker + 0.05);
}
