/**
 * TC-auth-15 — the admin breadcrumb renders page-level segments as Chinese
 * text and leaves the rest as the raw path (AC-51, C-15).
 *
 * The header breadcrumb is rendered against the shipped zh-CN catalog for the
 * admin console paths: page-level segments read as Chinese text, never as the
 * raw path segment, while dynamic identifiers, action segments and unregistered
 * segments keep the raw path text.
 */
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { pathnameRef } = vi.hoisted(() => ({ pathnameRef: { current: "/" } }));

vi.mock("next/navigation", () => ({
  usePathname: () => pathnameRef.current,
}));

import { Breadcrumb } from "@/components/layout/breadcrumb";
import { renderWithIntl } from "@/components/test-utils";

export const TC_ID = "TC-auth-15";

/** Visible breadcrumb text, one label per navigation step. */
function breadcrumbText(): string {
  const nav = screen.getByRole("navigation", { name: "面包屑" });
  return Array.from(nav.children)
    .map((node) => node.textContent?.trim() ?? "")
    .join(" > ");
}

function renderAt(pathname: string): string {
  pathnameRef.current = pathname;
  const { unmount } = renderWithIntl(<Breadcrumb />);
  const text = breadcrumbText();
  unmount();
  return text;
}

describe("TC-auth-15 管理端面包屑段名呈现", () => {
  it("names the run-log page segment in Chinese", () => {
    expect(renderAt("/admin/run-logs")).toBe("首页 > 管理 > 运行日志");
  });

  it("names the members page segment in Chinese", () => {
    expect(renderAt("/admin/users")).toBe("首页 > 管理 > 成员管理");
  });

  it("keeps the other page-level segments in Chinese", () => {
    expect(renderAt("/admin/knowledge")).toBe("首页 > 管理 > 知识库");
    expect(renderAt("/admin/agents")).toBe("首页 > 管理 > 智能体");
    expect(renderAt("/admin/tools")).toBe("首页 > 管理 > 工具");
  });

  it("keeps action segments as raw path text", () => {
    expect(renderAt("/admin/agents/new")).toBe("首页 > 管理 > 智能体 > new");
  });

  it("keeps dynamic identifiers as raw path text", () => {
    expect(renderAt("/admin/agents/agent-0001")).toBe(
      "首页 > 管理 > 智能体 > agent-0001",
    );
  });

  it("falls back to the raw segment when no name is registered", () => {
    expect(renderAt("/admin/unknown-segment")).toBe(
      "首页 > 管理 > unknown-segment",
    );
  });
});
