import { expect, type Page } from "@playwright/test";

import type { Conversation } from "@/lib/conversation-types";

// The browser's same-origin (the reverse proxy, `baseURL`). Derived from the
// same `E2E_PROXY_PORT` override the Playwright config reads, so cookie `url`
// fields stay correct when a local run uses a non-default port.
const PROXY_PORT = process.env.E2E_PROXY_PORT ?? "8090";
export const PROXY_ORIGIN = `http://localhost:${PROXY_PORT}`;

// The E2E suite asserts Chinese copy, so it pins the locale cookie explicitly
// instead of relying on the runtime default — changing `DEFAULT_LOCALE` must
// not silently break the whole suite.
const ZH_LOCALE_COOKIE = { name: "NEXT_LOCALE", value: "zh-CN" };

/** Standard `{ code, data, message }` success envelope. */
export function envelope<T>(data: T, message = ""): { code: number; data: T; message: string } {
  return { code: 0, data, message };
}

/** One SSE frame (`event:` + `data:` + blank line), per the §30.3 envelope. */
export function sseFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** The terminal `message_end` frame that completes a chat stream. */
export function messageEndFrame(
  traceId: string,
  metadata: Record<string, unknown> = {},
): string {
  return sseFrame("message_end", { traceId, metadata });
}

/** Complete the SSO login flow (mock IdP) and land on `/user`. */
export async function loginViaSso(page: Page): Promise<void> {
  await page.context().addCookies([{ ...ZH_LOCALE_COOKIE, url: PROXY_ORIGIN }]);
  await page.goto("/login");
  await page.getByRole("button", { name: "SSO 登录" }).click();
  await page.waitForURL("**/user");
  // Wait for the auth bootstrap to actually render the authenticated profile,
  // not merely the /user URL — a following full navigation must not race the
  // in-flight access-token bootstrap.
  await expect(page.getByText("alice@acme.com")).toBeVisible();
}

/** A mock conversation object shaped like `GET /api/conversations/{id}`. */
export function mockConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "conv-1",
    title: "项目讨论",
    agentId: "ag-1",
    agentName: "测试智能体",
    status: "active",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}
