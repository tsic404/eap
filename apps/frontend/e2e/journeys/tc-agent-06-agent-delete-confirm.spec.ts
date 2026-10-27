import { expect, test, type Locator, type Page } from "@playwright/test";

import type { AgentDetail } from "@/lib/agent-types";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope } from "../helpers/api";

// TC-agent-06 (AC-13) browser evidence: deleting an agent asks for its name and
// keeps the confirm action disabled until the typed name matches. C-49 fixes the
// comparison — trim, then case-sensitive exact equality.
//
// The criterion's third clause ("有关联会话时明确提示关联关系") is not asserted
// here: the detail DTO (C-35) exposes no conversation association and the delete
// endpoint reports none, so the dialog has no data to name. That clause needs a
// surface before a browser assertion can exist.

const TC_ID = "TC-agent-06";

const AGENT: AgentDetail = {
  agentId: "bot-1",
  tenantId: "tenant-1",
  difyAppId: "dify-app-1",
  name: "Support Bot",
  description: "回答售前咨询",
  type: "chat",
  category: "客服",
  icon: "🤖",
  tags: [],
  status: "draft",
  modelId: null,
  modelName: null,
  modelProvider: null,
  prompt: null,
  version: 1,
  createdBy: "user-1",
  publishedAt: null,
  publishedBy: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  boundKnowledge: [],
  boundTools: [],
  recentLogs: [],
};

async function signInAsAgentAdmin(page: Page): Promise<void> {
  const admin = LOCAL_ACCOUNTS.admin;
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(admin.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  await expect(page.getByText(admin.email)).toBeVisible();
}

async function openDeleteDialog(page: Page): Promise<Locator> {
  await page.goto("/admin/agents/bot-1");
  await page.getByRole("button", { name: "删除" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test(`${TC_ID} confirm: 名称不一致时确认按钮禁用，一致时才能删除`, async ({ page }) => {
  await page.route("**/api/agents/bot-1", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fulfill({ json: envelope(AGENT) });
  });
  await signInAsAgentAdmin(page);

  const dialog = await openDeleteDialog(page);
  const input = dialog.getByRole("textbox");
  await expect(input).toBeVisible();

  const confirm = dialog.getByRole("button", { name: "删除" });
  await expect(confirm).toBeDisabled();

  // Another case and a partial name both stay disabled.
  await input.fill("support bot");
  await expect(confirm).toBeDisabled();
  await input.fill("Support Bo");
  await expect(confirm).toBeDisabled();

  // Surrounding whitespace is trimmed before the comparison (C-49).
  await input.fill("  Support Bot  ");
  await expect(confirm).toBeEnabled();

  await input.fill("Support Bot");
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByRole("status").filter({ hasText: "已删除" })).toBeVisible();
  await page.waitForURL("**/admin");
});
