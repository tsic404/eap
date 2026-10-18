/**
 * TC-tech-ui-01 — semantic colour pair contrast (AC-T12).
 *
 * Contract: C-82/C-83/C-84/C-85/C-86. Every declared pair is recomputed from
 * the slot values in `app/globals.css` at run time, unrounded; the UI's class
 * lists are swept with cross-element inheritance modelled, so a semantic
 * colour that appears without a declared pair is a failure. Recorded
 * implementation deviations live in `KNOWN_GAPS` and are computed, not
 * skipped (the backend suite's `xfail(strict=False)` convention).
 *
 * Evidence boundary: slot values are recomputed from the stylesheet (C-84's
 * "静态色值只作复算辅助") and the sweep is a static model of the class lists.
 * No rendered computed-style or pixel arbitration is taken by this suite;
 * font size is asserted from the Tailwind scale (`text-xs` → 0.75rem), not
 * measured after layout.
 */
import { screen } from "@testing-library/react";
import defaultTheme from "tailwindcss/defaultTheme";
import { describe, expect, it } from "vitest";

import { ChunkList } from "@/components/knowledge/chunk-list";
import { renderWithIntl } from "@/components/test-utils";
import type { RetrievalCitation } from "@/lib/knowledge-types";
import tailwindConfig from "../../tailwind.config";

import { contrastRatio } from "./contrast";
import {
  COLOR_TOKENS,
  CONTRAST_THRESHOLDS,
  DECLARED_PAIRS,
  KNOWN_GAPS,
  MIN_LUMINANCE_GAP,
  hasLightnessSeparation,
  judgePair,
  meetsThreshold,
  pairKey,
  type DeclaredPair,
} from "./declared-pairs";
import { normalizeForeground, scanSemanticPairs } from "./usage-scan";

export const TC_ID = "TC-tech-ui-01";

const FAMILIES = [
  "primary",
  "success",
  "warning",
  "danger",
  "info",
  "neutral",
] as const;

const RECALL_TIERS = [
  {
    label: "分数 0.900",
    key: "success|success-foreground",
    classes: ["bg-success", "text-success-foreground"],
  },
  {
    label: "分数 0.600",
    key: "success-subtle|success",
    classes: ["bg-success-subtle", "text-success"],
  },
  {
    label: "分数 0.400",
    key: "warning-subtle|warning",
    classes: ["bg-warning-subtle", "text-warning"],
  },
] as const;

const FONT_SIZE_TOKENS = new Set([
  "text-xs",
  "text-sm",
  "text-base",
  "text-lg",
  "text-xl",
  "text-2xl",
  "text-3xl",
]);

function citation(score: number): RetrievalCitation {
  return {
    content: "退款政策：自签收之日起 7 日内可无理由退款。",
    score,
    source_name: "退款政策.pdf",
    kb_name: "产品知识库",
  };
}

function renderScores(scores: readonly number[]) {
  return renderWithIntl(
    <ChunkList
      citations={scores.map(citation)}
      query=""
      score={scores[0]}
      latencyMs={12}
    />,
  );
}

function classTokens(element: HTMLElement): string[] {
  return element.className.split(/\s+/);
}

function pairByKey(key: string): DeclaredPair {
  const pair = DECLARED_PAIRS.find((candidate) => pairKey(candidate) === key);
  if (!pair) throw new Error(`no declared pair ${key}`);
  return pair;
}

function isLightSlot(token: string): boolean {
  return token === "background" || token.endsWith("-subtle");
}

describe("TC-tech-ui-01 semantic colour pairs", () => {
  it("declares all six contract colour families with unique slot pairs", () => {
    for (const family of FAMILIES) {
      expect(
        DECLARED_PAIRS.some((pair) => pair.family === family),
        `family ${family}`,
      ).toBe(true);
    }
    expect(new Set(DECLARED_PAIRS.map(pairKey)).size).toBe(
      DECLARED_PAIRS.length,
    );
  });

  it("resolves every declared slot from the stylesheet at run time", () => {
    for (const pair of DECLARED_PAIRS) {
      expect(
        COLOR_TOKENS[pair.background.token],
        `--color-${pair.background.token}`,
      ).toMatch(/^#[0-9a-f]{6}$/i);
      expect(
        COLOR_TOKENS[pair.foreground],
        `--color-${pair.foreground}`,
      ).toMatch(/^#[0-9a-f]{6}$/i);
      expect(CONTRAST_THRESHOLDS[pair.textSize]).toBe(
        pair.textSize === "small" ? 4.5 : 3,
      );
    }
  });

  for (const family of FAMILIES) {
    it(`recomputes the ${family} pairs at their declared text-size threshold`, () => {
      const judgements = DECLARED_PAIRS.filter(
        (pair) => pair.family === family,
      ).map(judgePair);
      expect(judgements.length).toBeGreaterThan(0);
      for (const judgement of judgements) {
        expect(Number.isFinite(judgement.ratio)).toBe(true);
      }
      const regressions = judgements
        .filter(
          (judgement) =>
            !judgement.meets && KNOWN_GAPS[judgement.key] !== true,
        )
        .map(
          (judgement) =>
            `${judgement.key}: ${judgement.ratio.toFixed(2)} < ${judgement.threshold}`,
        );
      expect(regressions).toEqual([]);
    });
  }

  it("models cross-element inheritance for banners and muted surfaces", () => {
    const usage = scanSemanticPairs();
    for (const key of [
      "danger-subtle|muted-foreground",
      "primary-subtle|muted-foreground",
      "info-subtle|muted-foreground",
      "info-subtle|foreground",
      "warning-subtle|foreground",
    ]) {
      expect(usage.has(key), key).toBe(true);
    }
  });

  it("normalizes text-white only when the family foreground is white", () => {
    expect(normalizeForeground("white", "danger")).toBe("danger-foreground");
    expect(normalizeForeground("white", "muted")).toBe("white");
    expect(normalizeForeground("white", "background")).toBe("white");
    expect(normalizeForeground("inherit", "danger")).toBeUndefined();
  });

  it("renders the three recall tiers as 12px badges with distinct colours", () => {
    renderScores([0.9, 0.6, 0.4]);
    const badges = RECALL_TIERS.map((tier) => screen.getByText(tier.label));
    const extend = (
      tailwindConfig.theme as { extend?: { fontSize?: unknown } } | undefined
    )?.extend;
    expect(extend?.fontSize, "project overrides fontSize").toBeUndefined();
    expect(defaultTheme.fontSize.xs[0]).toBe("0.75rem");
    expect(Number.parseFloat(defaultTheme.fontSize.xs[0]) * 16).toBe(12);
    for (const badge of badges) {
      expect(
        classTokens(badge).filter((token) => FONT_SIZE_TOKENS.has(token)),
      ).toEqual(["text-xs"]);
    }
    expect(new Set(badges.map((badge) => badge.className)).size).toBe(3);
    expect(
      new Set([
        COLOR_TOKENS.success,
        COLOR_TOKENS["success-subtle"],
        COLOR_TOKENS["warning-subtle"],
      ]).size,
    ).toBe(3);
  });

  it("judges each recall tier pair at the 12px small-text threshold", () => {
    renderScores([0.9, 0.6, 0.4]);
    for (const tier of RECALL_TIERS) {
      const badge = screen.getByText(tier.label);
      for (const className of tier.classes) {
        expect(classTokens(badge)).toContain(className);
      }
      const pair = pairByKey(tier.key);
      expect(pair.textSize).toBe("small");
      const judgement = judgePair(pair);
      expect(
        judgement.meets || KNOWN_GAPS[judgement.key] === true,
        `${judgement.key}: ${judgement.ratio.toFixed(2)} < ${judgement.threshold} and not registered`,
      ).toBe(true);
    }
    expect(
      judgePair(pairByKey("background|foreground")).meets,
      "positive control",
    ).toBe(true);
  });

  it("judges unrounded ratios and composites translucent backgrounds (C-84)", () => {
    const translucent = judgePair(pairByKey("danger/90|danger-foreground"));
    expect(translucent.ratio).toBeCloseTo(4.32, 1);
    expect(translucent.meets).toBe(false);
    expect(KNOWN_GAPS[translucent.key]).toBe(true);
    // Without compositing the solid slot would pass: the verdict depends on it.
    expect(
      contrastRatio(
        COLOR_TOKENS["danger-foreground"],
        COLOR_TOKENS.danger,
        COLOR_TOKENS.background,
      ),
    ).toBeGreaterThanOrEqual(4.5);
    // A value just below the threshold that rounds to 4.50 must still fail.
    const nearThreshold = contrastRatio("#767776", "#ffffff");
    expect(nearThreshold).toBeLessThan(4.5);
    expect(Number(nearThreshold.toFixed(2))).toBe(4.5);
    expect(meetsThreshold(nearThreshold, "small")).toBe(false);
  });

  it("keeps score tiers distinguishable by text, not colour alone", () => {
    renderScores([0.9, 0.6, 0.4]);
    const labels = RECALL_TIERS.map((tier) => tier.label);
    for (const label of labels) {
      expect(screen.getByText(label).textContent).toMatch(
        /^分数 \d\.\d{3}$/,
      );
    }
    expect(new Set(labels).size).toBe(3);
  });

  it("uses no semantic pair outside the declaration", () => {
    const usage = scanSemanticPairs();
    expect(usage.size).toBeGreaterThan(0);
    const declared = new Set(DECLARED_PAIRS.map(pairKey));
    const undeclared = [...usage.values()]
      .filter((entry) => !declared.has(entry.key))
      .map((entry) => `${entry.key} at ${entry.locations.join(", ")}`);
    expect(undeclared).toEqual([]);
  });

  it("sweeps at least two colour families and the neutral surface", () => {
    const usage = scanSemanticPairs();
    const families = new Set(
      [...usage.values()]
        .map((entry) => entry.background)
        .filter((token) => token !== "background")
        .map((token) => token.split("-")[0]),
    );
    expect(families.size).toBeGreaterThanOrEqual(2);
    const neutral = [...usage.values()].filter((entry) =>
      ["background", "muted", "border"].includes(entry.background),
    );
    expect(neutral.length).toBeGreaterThan(0);
  });

  it("rejects same-lightness combinations by luminance separation (C-83)", () => {
    for (const pair of DECLARED_PAIRS) {
      expect(
        hasLightnessSeparation(pair),
        `${pairKey(pair)} |ΔL| < ${MIN_LUMINANCE_GAP}`,
      ).toBe(true);
      if (isLightSlot(pair.background.token)) {
        expect(
          isLightSlot(pair.foreground),
          `${pairKey(pair)} light-on-light`,
        ).toBe(false);
      }
    }
    const lightOnLight: DeclaredPair = {
      family: "neutral",
      background: { token: "background" },
      foreground: "warning-subtle",
      textSize: "small",
      usage: "negative control",
    };
    expect(hasLightnessSeparation(lightOnLight)).toBe(false);
    const solidOnSameSolid: DeclaredPair = {
      family: "success",
      background: { token: "success" },
      foreground: "success",
      textSize: "small",
      usage: "negative control",
    };
    expect(hasLightnessSeparation(solidOnSameSolid)).toBe(false);
  });
});
