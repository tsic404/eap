import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RetrievalCitation } from "@/lib/knowledge-types";

import { renderWithIntl } from "../test-utils";

import { ChunkList } from "./chunk-list";

function citation(overrides: Partial<RetrievalCitation> = {}): RetrievalCitation {
  return {
    content: "退款政策：自签收之日起 7 日内可无理由退款。",
    score: 0.9,
    source_name: "退款政策.pdf",
    kb_name: "产品知识库",
    ...overrides,
  };
}

function renderList(citations: RetrievalCitation[]) {
  return renderWithIntl(
    <ChunkList
      citations={citations}
      query=""
      score={citations[0]?.score ?? 0}
      latencyMs={12}
    />,
  );
}

describe("ChunkList", () => {
  it("renders the four card elements: source, knowledge base, score, excerpt", () => {
    renderList([citation()]);

    expect(screen.getByText("退款政策.pdf")).toBeTruthy();
    expect(screen.getByText("产品知识库")).toBeTruthy();
    expect(screen.getByText("分数 0.900")).toBeTruthy();
    expect(
      screen.getByText("退款政策：自签收之日起 7 日内可无理由退款。"),
    ).toBeTruthy();
  });

  it("colors the score badge by score bucket", () => {
    renderList([
      citation({ score: 0.8, source_name: "a.pdf" }),
      citation({ score: 0.799, source_name: "b.pdf" }),
      citation({ score: 0.5, source_name: "c.pdf" }),
      citation({ score: 0.499, source_name: "d.pdf" }),
    ]);

    const deepGreen = screen.getByText("分数 0.800").className;
    expect(deepGreen).toContain("bg-success");
    expect(deepGreen).not.toContain("bg-success-subtle");
    expect(deepGreen).toContain("text-success-foreground");

    const lightGreen = screen.getByText("分数 0.799").className;
    expect(lightGreen).toContain("bg-success-subtle");
    expect(lightGreen).toContain("text-success");

    expect(screen.getByText("分数 0.500").className).toContain(
      "bg-success-subtle",
    );

    const yellow = screen.getByText("分数 0.499").className;
    expect(yellow).toContain("bg-warning-subtle");
    expect(yellow).toContain("text-warning");
  });

  it("shows the empty state when nothing is recalled", () => {
    renderList([]);

    expect(screen.getByText("未召回相关内容")).toBeTruthy();
  });
});
