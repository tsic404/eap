/**
 * TC-kb-06 — recall test result ordering, card contents, result-count slider and
 * empty state (AC-20).
 *
 * The form drives the real `retrievalTest` service; only the shared axios
 * instance is stubbed, so the assertions cover the request the UI sends and the
 * cards it renders from the returned result. Upstream ranking is TC-tech-kb-03's
 * evidence, and score colours/contrast stay with TC-tech-ui-01 — neither is
 * re-judged here.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { ApiEnvelope } from "@/lib/auth-types";
import type {
  RetrievalCitation,
  RetrievalTestResult,
} from "@/lib/knowledge-types";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));

vi.mock("@/lib/http-client", () => ({
  apiClient: { get: vi.fn(), post: postMock },
}));

import { RetrievalTest } from "@/components/knowledge/retrieval-test";

import { renderWithIntl } from "@/components/test-utils";

export const TC_ID = "TC-kb-06";

const KB_ID = "kb-1";
const RETRIEVAL_URL = `/knowledge-bases/${KB_ID}/retrieval-test`;

function result(query: string, scores: readonly number[]): RetrievalTestResult {
  const citations: RetrievalCitation[] = scores.map((score, index) => ({
    content: `第 ${index + 1} 条召回片段：差旅费用需在 30 日内提交发票。`,
    score,
    source_name: `差旅制度-${index + 1}.pdf`,
    kb_name: "行政知识库",
  }));
  return {
    query,
    citations,
    score: citations[0]?.score ?? 0,
    latency_ms: 18,
  };
}

function respondWith(
  handler: (
    url: string,
    payload: { query: string; retrieval_model: { top_k: number } },
  ) => RetrievalTestResult,
) {
  postMock.mockImplementation(
    (
      url: string,
      payload: { query: string; retrieval_model: { top_k: number } },
    ): Promise<{ data: ApiEnvelope<RetrievalTestResult> }> =>
      Promise.resolve({
        data: { code: 0, data: handler(url, payload), message: "" },
      }),
  );
}

function renderForm() {
  return renderWithIntl(
    <ToastProvider>
      <RetrievalTest kbId={KB_ID} />
    </ToastProvider>,
  );
}

function slider(): HTMLInputElement {
  return screen.getByLabelText(/^Top K/) as HTMLInputElement;
}

async function runQuery(query: string, expectedCalls: number) {
  fireEvent.change(screen.getByLabelText("查询内容"), {
    target: { value: query },
  });
  fireEvent.click(screen.getByRole("button", { name: "运行测试" }));
  await waitFor(() => expect(postMock).toHaveBeenCalledTimes(expectedCalls));
}

describe("TC-kb-06 recall test results", () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it("renders the recalled cards in descending score order with every card element", async () => {
    respondWith((_url, payload) =>
      result(payload.query, [0.92, 0.71, 0.44]),
    );
    renderForm();

    await runQuery("报销流程", 1);
    const badges = await screen.findAllByText(/^分数 /);

    expect(badges.map((badge) => badge.textContent)).toEqual([
      "分数 0.920",
      "分数 0.710",
      "分数 0.440",
    ]);

    for (const index of [1, 2, 3]) {
      expect(screen.getByText(`差旅制度-${index}.pdf`)).toBeTruthy();
      expect(
        screen.getByText(
          `第 ${index} 条召回片段：差旅费用需在 30 日内提交发票。`,
        ),
      ).toBeTruthy();
    }
    expect(screen.getAllByText("行政知识库")).toHaveLength(3);
    expect(screen.getByText("命中 3 条")).toBeTruthy();
  });

  it("sends Top K 5 and then 10 as the slider moves, rendering that many hits", async () => {
    respondWith((_url, payload) => {
      const topK = payload.retrieval_model.top_k;
      const scores = Array.from(
        { length: topK },
        (_, index) => 0.95 - index * 0.06,
      );
      return result(payload.query, scores);
    });
    renderForm();

    expect(slider().value).toBe("5");
    expect(slider().min).toBe("5");
    expect(slider().max).toBe("10");

    await runQuery("差旅报销", 1);
    expect(screen.getAllByText(/^分数 /)).toHaveLength(5);
    expect(screen.getByText("命中 5 条")).toBeTruthy();
    expect(postMock).toHaveBeenNthCalledWith(1, RETRIEVAL_URL, {
      query: "差旅报销",
      retrieval_model: { top_k: 5 },
    });

    fireEvent.change(slider(), { target: { value: "10" } });
    expect(slider().value).toBe("10");
    expect(screen.getByText("Top K：10")).toBeTruthy();

    await runQuery("差旅报销", 2);
    await waitFor(() =>
      expect(screen.getAllByText(/^分数 /)).toHaveLength(10),
    );
    expect(screen.getByText("命中 10 条")).toBeTruthy();
    expect(postMock).toHaveBeenNthCalledWith(2, RETRIEVAL_URL, {
      query: "差旅报销",
      retrieval_model: { top_k: 10 },
    });
  });

  it("replaces previous hits with the empty state for an unrelated query", async () => {
    respondWith((_url, payload) =>
      payload.query === "量子纠缠"
        ? result(payload.query, [])
        : result(payload.query, [0.82, 0.61]),
    );
    renderForm();

    await runQuery("报销流程", 1);
    expect(await screen.findAllByText(/^分数 /)).toHaveLength(2);
    expect(screen.queryByText("未召回相关内容")).toBeNull();

    await runQuery("量子纠缠", 2);
    expect(await screen.findByText("未召回相关内容")).toBeTruthy();
    expect(
      screen.getByText("尝试调整查询内容或增大 Top K"),
    ).toBeTruthy();
    expect(screen.queryByText(/^分数 /)).toBeNull();
    expect(screen.queryByText(/^命中 /)).toBeNull();
  });
});
