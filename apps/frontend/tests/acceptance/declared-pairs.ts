/**
 * The semantic colour pairs the UI declares, with the text size each pair is
 * judged at (C-82/C-83/C-85). Slot values are read from `app/globals.css` at
 * run time, so a token change is re-judged by the same formulas instead of
 * being masked by a frozen copy — TC-tech-ui-01's regression clause.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  composite,
  contrastRatio,
  parseColor,
  relativeLuminance,
} from "./contrast";
import { FRONTEND_ROOT } from "./paths";

const STYLESHEET = path.join(FRONTEND_ROOT, "app/globals.css");
const ROOT_BLOCK = /:root\s*\{([\s\S]*?)\}/;
const TOKEN_DECLARATION = /--color-([a-z0-9-]+)\s*:\s*([^;]+);/g;

function readTokens(): Readonly<Record<string, string>> {
  const block = readFileSync(STYLESHEET, "utf8").match(ROOT_BLOCK)?.[1];
  if (!block) {
    throw new Error("app/globals.css declares no :root colour tokens");
  }
  const tokens: Record<string, string> = {};
  for (const match of block.matchAll(TOKEN_DECLARATION)) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

export const COLOR_TOKENS = readTokens();

export interface ColorSlot {
  /** `--color-<token>` in the stylesheet. */
  token: string;
  /** Tailwind alpha suffix in percent: `bg-danger/90` → 90. */
  alpha?: number;
  /** Slot a translucent background composites onto (defaults to background). */
  parent?: string;
}

export type TextSize = "small" | "large";

export interface DeclaredPair {
  family: "primary" | "success" | "warning" | "danger" | "info" | "neutral";
  background: ColorSlot;
  foreground: string;
  textSize: TextSize;
  usage: string;
}

export const CONTRAST_THRESHOLDS: Readonly<Record<TextSize, number>> = {
  small: 4.5,
  large: 3,
};

/** C-83 bans same-luminance combinations; this is the minimum separation. */
export const MIN_LUMINANCE_GAP = 0.25;

/**
 * Every pair the UI uses, by slot. `text-white` in the class lists is the
 * matching `-foreground` slot; the scan normalizes it before lookup.
 *
 * Every pair is judged at the small-text threshold: each one is used on small
 * text somewhere (buttons, badges, body copy), and C-82 judges mixed-size
 * usage at the strictest tier. No pair is used only at large text, so the
 * ≥3:1 branch is not exercised by this declaration.
 */
export const DECLARED_PAIRS: readonly DeclaredPair[] = [
  // primary — solid actions, their interaction states, and the subtle icon bed.
  {
    family: "primary",
    background: { token: "primary" },
    foreground: "primary-foreground",
    textSize: "small",
    usage: "实心主按钮 / 主色头像（默认态）",
  },
  {
    family: "primary",
    background: { token: "primary-hover" },
    foreground: "primary-foreground",
    textSize: "small",
    usage: "实心主按钮 hover",
  },
  {
    family: "primary",
    background: { token: "primary-active" },
    foreground: "primary-foreground",
    textSize: "small",
    usage: "实心主按钮 active",
  },
  {
    family: "primary",
    background: { token: "primary-subtle" },
    foreground: "primary",
    textSize: "small",
    usage: "主色浅底图标 / 强调容器",
  },
  {
    family: "primary",
    background: { token: "primary-subtle" },
    foreground: "foreground",
    textSize: "small",
    usage: "主色浅底容器内正文（勾选工具项）",
  },
  {
    family: "primary",
    background: { token: "primary-subtle" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "主色浅底容器内辅助文字（拖拽区 / 勾选工具项）",
  },
  {
    family: "primary",
    background: { token: "background" },
    foreground: "primary",
    textSize: "small",
    usage: "页面上的主色文字 / 链接",
  },
  // success — recall ≥0.8 badge, success actions, success text.
  {
    family: "success",
    background: { token: "success" },
    foreground: "success-foreground",
    textSize: "small",
    usage: "召回 ≥0.8 深绿徽章 / 成功按钮",
  },
  {
    family: "success",
    background: { token: "success", alpha: 90 },
    foreground: "success-foreground",
    textSize: "small",
    usage: "成功按钮 hover",
  },
  {
    family: "success",
    background: { token: "success-subtle" },
    foreground: "success",
    textSize: "small",
    usage: "召回 ≥0.5 浅绿徽章 / 成功徽章与时间线",
  },
  {
    family: "success",
    background: { token: "background" },
    foreground: "success",
    textSize: "small",
    usage: "页面上的成功文字 / 图标",
  },
  {
    family: "success",
    background: { token: "muted" },
    foreground: "success",
    textSize: "small",
    usage: "健康状态正常图标（浅灰底）",
  },
  // warning — recall <0.5 badge, warning banner, warning text.
  {
    family: "warning",
    background: { token: "warning" },
    foreground: "warning-foreground",
    textSize: "small",
    usage: "警告实心槽位",
  },
  {
    family: "warning",
    background: { token: "warning-subtle" },
    foreground: "warning",
    textSize: "small",
    usage: "召回 <0.5 黄色徽章 / 警告横幅与时间线",
  },
  {
    family: "warning",
    background: { token: "warning-subtle" },
    foreground: "foreground",
    textSize: "small",
    usage: "警告横幅内正文",
  },
  {
    family: "warning",
    background: { token: "warning-subtle" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "召回命中高亮 mark 内的辅助文字",
  },
  {
    family: "warning",
    background: { token: "muted" },
    foreground: "warning",
    textSize: "small",
    usage: "健康状态告警图标（浅灰底）",
  },
  {
    family: "warning",
    background: { token: "background" },
    foreground: "warning",
    textSize: "small",
    usage: "页面上的警告文字 / 图标",
  },
  // danger — destructive actions, banners, badges, list hover.
  {
    family: "danger",
    background: { token: "danger" },
    foreground: "danger-foreground",
    textSize: "small",
    usage: "危险实心按钮（默认 / active）",
  },
  {
    family: "danger",
    background: { token: "danger", alpha: 90 },
    foreground: "danger-foreground",
    textSize: "small",
    usage: "危险按钮 hover",
  },
  {
    family: "danger",
    background: { token: "danger-subtle" },
    foreground: "danger",
    textSize: "small",
    usage: "危险横幅 / 徽章 / 时间线",
  },
  {
    family: "danger",
    background: { token: "danger-subtle" },
    foreground: "foreground",
    textSize: "small",
    usage: "危险横幅内正文",
  },
  {
    family: "danger",
    background: { token: "danger-subtle" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "危险横幅内辅助文字",
  },
  {
    family: "danger",
    background: { token: "background" },
    foreground: "danger",
    textSize: "small",
    usage: "页面上的危险文字 / 图标",
  },
  {
    family: "danger",
    background: { token: "muted" },
    foreground: "danger",
    textSize: "small",
    usage: "列表项 hover 的危险图标 / 健康状态严重图标",
  },
  // info — informational actions, badges, banners, timeline.
  {
    family: "info",
    background: { token: "info" },
    foreground: "info-foreground",
    textSize: "small",
    usage: "信息实心按钮（默认 / active）",
  },
  {
    family: "info",
    background: { token: "info", alpha: 90 },
    foreground: "info-foreground",
    textSize: "small",
    usage: "信息按钮 hover",
  },
  {
    family: "info",
    background: { token: "info-subtle" },
    foreground: "info",
    textSize: "small",
    usage: "信息徽章 / 横幅 / 时间线",
  },
  {
    family: "info",
    background: { token: "info-subtle" },
    foreground: "foreground",
    textSize: "small",
    usage: "信息横幅内正文",
  },
  {
    family: "info",
    background: { token: "info-subtle" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "信息横幅内辅助文字",
  },
  {
    family: "info",
    background: { token: "background" },
    foreground: "info",
    textSize: "small",
    usage: "页面上的信息文字 / 图标",
  },
  // neutral — page and muted surfaces.
  {
    family: "neutral",
    background: { token: "background" },
    foreground: "foreground",
    textSize: "small",
    usage: "页面正文 / 默认前景",
  },
  {
    family: "neutral",
    background: { token: "background" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "页面辅助文字",
  },
  {
    family: "neutral",
    background: { token: "muted" },
    foreground: "foreground",
    textSize: "small",
    usage: "浅底容器正文",
  },
  {
    family: "neutral",
    background: { token: "muted" },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "默认徽章 / 空状态图标",
  },
  {
    family: "neutral",
    background: { token: "muted", alpha: 40 },
    foreground: "foreground",
    textSize: "small",
    usage: "拖拽上传区正文（40% 浅底）",
  },
  {
    family: "neutral",
    background: { token: "muted", alpha: 40 },
    foreground: "muted-foreground",
    textSize: "small",
    usage: "拖拽上传区辅助文字（40% 浅底）",
  },
  {
    family: "neutral",
    background: { token: "border" },
    foreground: "foreground",
    textSize: "small",
    usage: "次级按钮 hover 底",
  },
];

/**
 * Recorded implementation deviations from C-83/C-85 at the current baseline.
 * They are computed, never skipped; a failing pair listed here does not fail
 * the suite, mirroring the backend suite's `xfail(strict=False)` markers.
 * Remove an entry when the implementation fix lands.
 */
export const KNOWN_GAPS: Readonly<Record<string, true>> = {};

export function pairKey(pair: DeclaredPair): string {
  const suffix =
    pair.background.alpha === undefined ? "" : `/${pair.background.alpha}`;
  return `${pair.background.token}${suffix}|${pair.foreground}`;
}

/** Composes a slot value with a Tailwind alpha suffix for the formula. */
export function slotColor(slot: ColorSlot): string {
  const value = COLOR_TOKENS[slot.token];
  if (!value) {
    throw new Error(`declared pair uses missing slot --color-${slot.token}`);
  }
  if (slot.alpha === undefined) return value;
  const { r, g, b } = parseColor(value);
  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${slot.alpha / 100})`;
}

/** Relative luminance of a background slot after alpha compositing (C-84). */
export function slotLuminance(slot: ColorSlot): number {
  const value = parseColor(slotColor(slot));
  const parent = parseColor(COLOR_TOKENS[slot.parent ?? "background"]);
  return relativeLuminance(value.a < 1 ? composite(value, parent) : value);
}

/**
 * C-83's same-luminance ban, stated quantitatively: a background and its
 * foreground must be separated by at least {@link MIN_LUMINANCE_GAP} in
 * relative luminance (light-on-light and solid-on-same-solid fall below it).
 */
export function hasLightnessSeparation(pair: DeclaredPair): boolean {
  const foreground = COLOR_TOKENS[pair.foreground];
  if (!foreground) {
    throw new Error(
      `declared pair uses missing slot --color-${pair.foreground}`,
    );
  }
  const foregroundLuminance = relativeLuminance(parseColor(foreground));
  return (
    Math.abs(slotLuminance(pair.background) - foregroundLuminance) >=
    MIN_LUMINANCE_GAP
  );
}

/** Unrounded threshold comparison (C-84: rounding must not flip a verdict). */
export function meetsThreshold(ratio: number, textSize: TextSize): boolean {
  return ratio >= CONTRAST_THRESHOLDS[textSize];
}

export interface PairJudgement {
  key: string;
  pair: DeclaredPair;
  ratio: number;
  threshold: number;
  meets: boolean;
}

/** Recomputes one declared pair at its declared text-size threshold. */
export function judgePair(pair: DeclaredPair): PairJudgement {
  const background = slotColor(pair.background);
  const foreground = COLOR_TOKENS[pair.foreground];
  if (!foreground) {
    throw new Error(
      `declared pair uses missing slot --color-${pair.foreground}`,
    );
  }
  const parent = COLOR_TOKENS[pair.background.parent ?? "background"];
  const ratio = contrastRatio(foreground, background, parent);
  const threshold = CONTRAST_THRESHOLDS[pair.textSize];
  return {
    key: pairKey(pair),
    pair,
    ratio,
    threshold,
    meets: meetsThreshold(ratio, pair.textSize),
  };
}
