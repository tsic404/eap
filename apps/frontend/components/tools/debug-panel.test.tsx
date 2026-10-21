import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));

vi.mock("@/lib/http-client", () => ({
  apiClient: { post: apiPostMock },
}));

import { ToastProvider } from "@/components/ui/toast";
import type { DebugToolResult } from "@/lib/tool-types";

import { renderWithIntl } from "../test-utils";
import { DebugPanel } from "./debug-panel";

// Debug payload field names follow the tool domain snake_case convention (C-11),
// so the mock mirrors the wire body the backend returns (C-67).
const DEBUG_RESULT: DebugToolResult = {
  status_code: 200,
  response_body: { city: "北京", temperature: 26 },
  latency_ms: 128,
  saved_case_id: "case-7",
};

async function runDebug() {
  fireEvent.click(screen.getByRole("button", { name: "发送请求" }));
  await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
}

describe("DebugPanel", () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    apiPostMock.mockResolvedValue({
      data: { code: 0, data: DEBUG_RESULT, message: "" },
    });
  });

  it("shows status code, latency, response body and saved-case marker for a 2xx debug response", async () => {
    renderWithIntl(
      <ToastProvider>
        <DebugPanel toolId="tool-1" />
      </ToastProvider>,
    );

    await runDebug();

    const badge = await screen.findByText("200");
    expect(badge.className).toContain("text-success");
    expect(screen.getByText("耗时 128 ms")).toBeTruthy();
    expect(screen.getByText(/temperature/).textContent).toContain(
      '"temperature": 26',
    );
    expect(screen.getByText("已保存用例")).toBeTruthy();
    expect(screen.getByText("用例已保存")).toBeTruthy();
  });

  it("flags a 4xx/5xx debug response in red and shows no saved-case marker", async () => {
    apiPostMock.mockResolvedValue({
      data: {
        code: 0,
        data: { ...DEBUG_RESULT, status_code: 502, saved_case_id: null },
        message: "",
      },
    });
    renderWithIntl(
      <ToastProvider>
        <DebugPanel toolId="tool-1" />
      </ToastProvider>,
    );

    await runDebug();

    const badge = await screen.findByText("502");
    expect(badge.className).toContain("text-danger");
    expect(screen.getByText("耗时 128 ms")).toBeTruthy();
    expect(screen.queryByText("已保存用例")).toBeNull();
    expect(screen.queryByText("用例已保存")).toBeNull();
  });
});
