/**
 * Tool registration form control shapes: the five type cards, the three-band
 * risk slider and the mutually exclusive permission radios, plus the save
 * feedback and the payload those controls produce.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { ToolDetail } from "@/lib/tool-types";

import { renderWithIntl } from "../test-utils";

const mocks = vi.hoisted(() => ({
  createTool: vi.fn(),
  updateTool: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: mocks.replace,
    push: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

vi.mock("@/lib/tool-service", () => ({
  createTool: mocks.createTool,
  updateTool: mocks.updateTool,
}));

import { ToolConfigForm } from "./tool-config-form";

const CREATED_TOOL = {
  tool_id: "weather-query",
  tenant_id: "tenant-1",
  name: "天气查询",
  description: null,
  type: "database",
  endpoint: "https://api.example.com/weather",
  method: "POST",
  risk_level: "high",
  permission_mode: "confirm",
  auth_type: "none",
  auth_config: null,
  timeout_ms: 10000,
  retry_policy: null,
  circuit_breaker: null,
  status: "active",
  created_by: null,
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T00:00:00Z",
} as ToolDetail;

function renderForm() {
  return renderWithIntl(
    <ToastProvider>
      <ToolConfigForm />
    </ToastProvider>,
  );
}

function checkedRadios(groupName: string): string[] {
  const group = screen.getByRole("radiogroup", { name: groupName });
  return within(group)
    .getAllByRole("radio")
    .filter((radio) => (radio as HTMLInputElement).checked)
    .map((radio) => (radio as HTMLInputElement).value);
}

beforeEach(() => {
  mocks.createTool.mockReset();
  mocks.updateTool.mockReset();
  mocks.replace.mockReset();
  mocks.createTool.mockResolvedValue(CREATED_TOOL);
});

describe("ToolConfigForm", () => {
  it("offers the five tool types as exclusive cards", () => {
    renderForm();

    const group = screen.getByRole("radiogroup", { name: "类型" });
    const cards = within(group).getAllByRole("radio");
    expect(cards).toHaveLength(5);
    expect(
      cards.map((card) => card.closest("label")?.textContent?.trim()),
    ).toEqual(["HTTP", "数据库", "RPA", "Webhook", "MCP"]);

    expect(checkedRadios("类型")).toEqual(["http"]);

    fireEvent.click(within(group).getByRole("radio", { name: "数据库" }));

    expect(checkedRadios("类型")).toEqual(["database"]);
  });

  it("moves the risk level across the three colour-coded slider stops", () => {
    const { container } = renderForm();

    const slider = screen.getByRole("slider", { name: "风险等级" });
    expect(slider.getAttribute("aria-valuetext")).toBe("中风险");

    const stops = Array.from(
      container.querySelectorAll<HTMLElement>("[data-risk-stop]"),
    );
    expect(stops.map((stop) => stop.dataset.riskStop)).toEqual([
      "low",
      "medium",
      "high",
    ]);
    expect(stops[0].className).toContain("bg-success");
    expect(stops[1].className).toContain("bg-warning");
    expect(stops[2].className).toContain("bg-danger");

    fireEvent.change(slider, { target: { value: "2" } });
    expect(
      screen.getByRole("slider", { name: "风险等级" }).getAttribute(
        "aria-valuetext",
      ),
    ).toBe("高风险");

    fireEvent.change(screen.getByRole("slider", { name: "风险等级" }), {
      target: { value: "0" },
    });
    expect(
      screen.getByRole("slider", { name: "风险等级" }).getAttribute(
        "aria-valuetext",
      ),
    ).toBe("低风险");
  });

  it("keeps permission mode a single exclusive choice", () => {
    renderForm();

    const group = screen.getByRole("radiogroup", { name: "权限模式" });
    const options = within(group).getAllByRole("radio");
    expect(options).toHaveLength(3);

    expect(checkedRadios("权限模式")).toEqual(["auto"]);

    fireEvent.click(within(group).getByRole("radio", { name: "需确认" }));

    expect(checkedRadios("权限模式")).toEqual(["confirm"]);
  });

  it("submits the chosen type, risk and permission and confirms the save", async () => {
    renderForm();

    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "天气查询" },
    });
    fireEvent.change(screen.getByLabelText("标识（tool_id）"), {
      target: { value: "weather-query" },
    });
    fireEvent.change(screen.getByLabelText("端点（Endpoint）"), {
      target: { value: "https://api.example.com/weather" },
    });

    fireEvent.click(screen.getByRole("radio", { name: "数据库" }));
    fireEvent.change(screen.getByRole("slider", { name: "风险等级" }), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "需确认" }));

    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => expect(mocks.createTool).toHaveBeenCalledTimes(1));
    expect(mocks.createTool.mock.calls[0][0]).toMatchObject({
      name: "天气查询",
      tool_id: "weather-query",
      type: "database",
      risk_level: "high",
      permission_mode: "confirm",
      endpoint: "https://api.example.com/weather",
    });

    expect((await screen.findByRole("status")).textContent).toContain(
      "工具已创建",
    );
    expect(mocks.replace).toHaveBeenCalledWith("/admin/tools/weather-query");
  });
});
