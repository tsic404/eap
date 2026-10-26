import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, PROXY_ORIGIN } from "../helpers/api";

// AC-20 / TC-kb-06 browser-side evidence: the recall-test tab renders hits in
// descending score order, applies the 5→10 Top K slider to both the request and
// the visible hit count, and shows the empty state for an unrelated query.
// The retrieval endpoint is replayed here; upstream ranking is TC-tech-kb-03's
// evidence and score-colour contrast stays with TC-tech-ui-01.

const KNOWLEDGE_BASE = {
  kb_id: "kb-1",
  name: "产品知识库",
  description: "产品手册与 FAQ",
  type: "business",
  indexing_status: "ready",
  doc_count: 0,
  chunk_count: 0,
  created_at: "2026-01-01T00:00:00Z",
};

const RETRIEVAL_URL = "**/api/knowledge-bases/kb-1/retrieval-test";

test("recall test orders hits, applies Top K and shows the empty state", async ({
  page,
  context,
}) => {
  await context.addCookies([
    { name: "eap_role", value: "agent_admin", url: PROXY_ORIGIN },
  ]);
  await loginViaSso(page);

  await page.route("**/api/knowledge-bases/kb-1", (route) =>
    route.fulfill({ json: envelope(KNOWLEDGE_BASE) }),
  );
  await page.route("**/api/knowledge-bases/kb-1/documents*", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0 }) }),
  );

  const requests: { query: string; topK: number }[] = [];
  await page.route(RETRIEVAL_URL, async (route) => {
    const body = route.request().postDataJSON() as {
      query: string;
      retrieval_model: { top_k: number };
    };
    requests.push({ query: body.query, topK: body.retrieval_model.top_k });
    const citations =
      body.query === "量子纠缠"
        ? []
        : Array.from(
            { length: body.retrieval_model.top_k },
            (_, index) => 0.95 - index * 0.06,
          ).map((score, index) => ({
            content: `第 ${index + 1} 条召回片段：差旅费用需在 30 日内提交发票。`,
            score,
            source_name: `差旅制度-${index + 1}.pdf`,
            kb_name: "行政知识库",
          }));
    await route.fulfill({
      json: envelope({
        query: body.query,
        citations,
        score: citations[0]?.score ?? 0,
        latency_ms: 18,
      }),
    });
  });

  await page.goto("/admin/knowledge/kb-1");
  await page.getByRole("tab", { name: "召回测试" }).click();

  const query = page.getByLabel("查询内容");
  const slider = page.getByLabel(/^Top K/);
  const run = page.getByRole("button", { name: "运行测试" });

  await expect(slider).toHaveValue("5");
  await query.fill("报销流程");
  await run.click();

  const firstBadges = page.getByText(/^分数 /);
  await expect(firstBadges).toHaveCount(5);
  await expect(page.getByText("命中 5 条")).toBeVisible();
  expect(requests).toEqual([{ query: "报销流程", topK: 5 }]);
  expect(await firstBadges.allTextContents()).toEqual([
    "分数 0.950",
    "分数 0.890",
    "分数 0.830",
    "分数 0.770",
    "分数 0.710",
  ]);

  await expect(page.getByText("差旅制度-1.pdf")).toBeVisible();
  await expect(page.getByText("行政知识库").first()).toBeVisible();
  await expect(
    page.getByText("第 1 条召回片段：差旅费用需在 30 日内提交发票。"),
  ).toBeVisible();

  await slider.focus();
  for (let step = 0; step < 5; step += 1) {
    await slider.press("ArrowRight");
  }
  await expect(slider).toHaveValue("10");
  await expect(page.getByText("Top K：10")).toBeVisible();

  await run.click();
  await expect(firstBadges).toHaveCount(10);
  await expect(page.getByText("命中 10 条")).toBeVisible();
  expect(requests).toEqual([
    { query: "报销流程", topK: 5 },
    { query: "报销流程", topK: 10 },
  ]);

  await query.fill("量子纠缠");
  await run.click();
  await expect(page.getByText("未召回相关内容")).toBeVisible();
  await expect(page.getByText("尝试调整查询内容或增大 Top K")).toBeVisible();
  await expect(firstBadges).toHaveCount(0);
  await expect(page.getByText(/^命中 /)).toHaveCount(0);
});
