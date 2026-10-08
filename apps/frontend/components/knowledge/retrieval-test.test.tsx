import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { RetrievalTestResult } from "@/lib/knowledge-types";

import { renderWithIntl } from "../test-utils";

const { retrievalTestMock } = vi.hoisted(() => ({
  retrievalTestMock: vi.fn(),
}));

vi.mock("@/lib/knowledge-service", () => ({
  retrievalTest: retrievalTestMock,
}));

import { RetrievalTest } from "./retrieval-test";

const RESULT: RetrievalTestResult = {
  query: "退款政策",
  citations: [
    {
      content: "退款政策：自签收之日起 7 日内可无理由退款。",
      score: 0.9,
      source_name: "退款政策.pdf",
      kb_name: "产品知识库",
    },
  ],
  score: 0.9,
  latencyMs: 12,
};

function renderForm() {
  return renderWithIntl(
    <ToastProvider>
      <RetrievalTest kbId="kb-1" />
    </ToastProvider>,
  );
}

function slider(): HTMLInputElement {
  return screen.getByLabelText(/^Top K/) as HTMLInputElement;
}

async function runQuery(query: string) {
  fireEvent.change(screen.getByLabelText("查询内容"), {
    target: { value: query },
  });
  fireEvent.click(screen.getByRole("button", { name: "运行测试" }));
  await waitFor(() => expect(retrievalTestMock).toHaveBeenCalled());
}

describe("RetrievalTest", () => {
  beforeEach(() => {
    retrievalTestMock.mockReset();
    retrievalTestMock.mockResolvedValue(RESULT);
  });

  it("defaults the Top K slider to 5 with a 5→10 range", () => {
    renderForm();

    expect(slider().value).toBe("5");
    expect(slider().min).toBe("5");
    expect(slider().max).toBe("10");
    expect(screen.getByText("Top K：5")).toBeTruthy();
  });

  it("runs the query with the default Top K and renders the recalled cards", async () => {
    renderForm();

    await runQuery("退款政策");

    expect(retrievalTestMock).toHaveBeenCalledWith("kb-1", {
      query: "退款政策",
      retrieval_model: { top_k: 5 },
    });
    expect(await screen.findByText("产品知识库")).toBeTruthy();
    expect(screen.getByText("分数 0.900")).toBeTruthy();
  });

  it("sends the Top K selected on the slider", async () => {
    renderForm();

    fireEvent.change(slider(), { target: { value: "10" } });
    expect(screen.getByText("Top K：10")).toBeTruthy();
    await runQuery("退款政策");

    expect(retrievalTestMock).toHaveBeenCalledWith("kb-1", {
      query: "退款政策",
      retrieval_model: { top_k: 10 },
    });
  });
});
