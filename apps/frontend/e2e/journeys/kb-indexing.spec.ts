import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, PROXY_ORIGIN } from "../helpers/api";

const KNOWLEDGE_BASE = {
  kb_id: "kb-1",
  name: "产品知识库",
  description: "产品手册与 FAQ",
  type: "business",
  indexing_status: "ready",
  doc_count: 1,
  chunk_count: 0,
  created_at: "2026-01-01T00:00:00Z",
};

const INDEXING_DOCUMENT = {
  id: "doc-1",
  name: "产品手册.pdf",
  status: "indexing",
};

// P0 journey §31.2.7: a document still indexing renders an in-progress badge and
// a progress indicator (and the list keeps polling until indexing completes).
test("knowledge-base document shows an indexing wait state", async ({ page, context }) => {
  await context.addCookies([
    { name: "eap_role", value: "agent_admin", url: PROXY_ORIGIN },
  ]);
  await loginViaSso(page);

  await page.route("**/api/knowledge-bases/kb-1", (route) =>
    route.fulfill({ json: envelope(KNOWLEDGE_BASE) }),
  );
  await page.route("**/api/knowledge-bases/kb-1/documents*", (route) =>
    route.fulfill({
      json: envelope({ items: [INDEXING_DOCUMENT], total: 1 }),
    }),
  );

  await page.goto("/admin/knowledge/kb-1");

  await expect(page.getByText("产品手册.pdf")).toBeVisible();
  await expect(page.getByText("索引中")).toBeVisible();
});
