import { expect, test, type Page } from "@playwright/test";

import { envelope, loginViaSso, PROXY_ORIGIN } from "../helpers/api";

// AC-15 / TC-kb-01 browser-side evidence: a successful create shows the toast
// and adds a card carrying name, description, type, document count, chunk count
// and the status badge; a duplicate name is blocked at the name field with the
// contract copy. The knowledge-base list endpoint is replayed here; name
// uniqueness itself is the backend's evidence.

const EXISTING = {
  kb_id: "kb-1",
  name: "产品手册",
  description: "产品说明与 FAQ",
  type: "business",
  indexing_status: "ready",
  doc_count: 3,
  chunk_count: 42,
  created_at: "2026-01-01T00:00:00Z",
};

const DIALOG_NAME = "新建知识库";

function card(page: Page, name: string) {
  return page
    .locator("div.rounded-lg")
    .filter({ has: page.getByRole("heading", { name }) });
}

async function openCreateDialog(page: Page) {
  await page.getByRole("button", { name: DIALOG_NAME }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: DIALOG_NAME })).toBeVisible();
  return dialog;
}

test("creating a knowledge base adds a card with all six elements", async ({
  page,
  context,
}) => {
  await context.addCookies([
    { name: "eap_role", value: "agent_admin", url: PROXY_ORIGIN },
  ]);
  await loginViaSso(page);

  const items = [EXISTING];
  await page.route("**/api/knowledge-bases*", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as {
        name: string;
        description: string;
        type: string;
      };
      const created = {
        kb_id: "kb-2",
        ...body,
        indexing_status: "ready",
        doc_count: 0,
        chunk_count: 0,
        created_at: "2026-10-27T00:00:00Z",
      };
      items.push(created);
      await route.fulfill({ status: 201, json: envelope(created) });
      return;
    }
    await route.fulfill({ json: envelope({ items, total: items.length }) });
  });

  await page.goto("/admin/knowledge");
  await expect(card(page, EXISTING.name)).toBeVisible();

  const dialog = await openCreateDialog(page);
  await dialog.getByLabel("名称").fill("探针知识库");
  await dialog.getByLabel("描述").fill("探针描述");
  await dialog.getByRole("button", { name: "创建" }).click();

  await expect(page.getByText("知识库已创建")).toBeVisible();

  const created = card(page, "探针知识库");
  await expect(created).toBeVisible();
  await expect(created.getByText("探针描述")).toBeVisible();
  await expect(created.getByText("类型 业务")).toBeVisible();
  await expect(created.getByText("文档 0")).toBeVisible();
  await expect(created.getByText("分块 0")).toBeVisible();
  await expect(created.getByText("就绪")).toBeVisible();
});

test("a duplicate name is blocked at the name field", async ({ page, context }) => {
  await context.addCookies([
    { name: "eap_role", value: "agent_admin", url: PROXY_ORIGIN },
  ]);
  await loginViaSso(page);

  await page.route("**/api/knowledge-bases*", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 409,
        json: { error: { code: "KB_NAME_EXISTS", message: "知识库名称已存在" } },
      });
      return;
    }
    await route.fulfill({ json: envelope({ items: [EXISTING], total: 1 }) });
  });

  await page.goto("/admin/knowledge");
  await expect(card(page, EXISTING.name)).toBeVisible();

  const dialog = await openCreateDialog(page);
  await dialog.getByLabel("名称").fill(EXISTING.name);
  await dialog.getByRole("button", { name: "创建" }).click();

  await expect(dialog.getByText("知识库名称已存在")).toBeVisible();
  await expect(dialog.getByLabel("名称")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("heading", { level: 3 })).toHaveCount(1);
  await expect(page.getByText("知识库已创建")).toBeHidden();
});
