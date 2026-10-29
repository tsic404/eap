import { expect, test, type Page } from "@playwright/test";

import { loadFixtures, metricValue, openApiSession, signIn } from "../helpers/fixtures";

// Browser-side evidence for the admin dashboard. The fixture seeds today's run
// logs (9 calls, one failed, every call 1200 ms) and three agents into a tenant
// of its own, so the four metric values, the trend's buckets and the ranking
// order are exact rather than "the card rendered something" — and no other
// journey's rows can enter the totals.

const TC_ID = "TC-ops-01";

interface TrendPoint {
  hour: string;
  calls: number;
}

interface AdminDashboardPayload {
  metrics: {
    totalAgents: number;
    todayCalls: number;
    avgLatencyMs: number | null;
    errorRate: number;
  };
  trend: TrendPoint[];
}

async function openDashboard(page: Page, accountEmail: string) {
  await signIn(page, { email: accountEmail });
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "仪表盘" })).toBeVisible();
}

/** `HH:00` bucket label, formatted the way the chart formats it (browser clock). */
async function bucketLabel(page: Page, hour: string): Promise<string> {
  return page.evaluate((iso) => {
    const date = new Date(iso);
    return `${String(date.getHours()).padStart(2, "0")}:00`;
  }, hour);
}

test.describe(TC_ID, () => {
  test("四维指标卡片齐全且数值与夹具一致", async ({ page, playwright }) => {
    const fixtures = loadFixtures();
    const account = { email: fixtures.dashboard.accountEmail };
    const api = await openApiSession(playwright.request, account);
    const response = await api.get("/api/dashboard/admin");
    expect(response.status()).toBe(200);
    // The envelope is the platform's fixed `{ code, data, message }` shape.
    const dashboardBody = (await response.json()) as { data: AdminDashboardPayload };
    const dashboard = dashboardBody.data;
    // 智能体总数 is tenant-wide, so it is compared with the agents list the same
    // tenant serves; the other three come from the seeded log table exactly.
    const agents = await api.get("/api/agents", { params: { pageSize: 100 } });
    expect(agents.status()).toBe(200);
    // The list answers with the platform envelope; `total` is the page's total.
    const agentsBody = (await agents.json()) as { data: { total: number } };
    const agentTotal = agentsBody.data.total;
    await api.dispose();

    await openDashboard(page, account.email);

    await expect(metricValue(page, "智能体总数")).toHaveText(String(agentTotal));
    expect(agentTotal).toBeGreaterThanOrEqual(fixtures.dashboard.ranking.length);

    await expect(metricValue(page, "今日调用")).toHaveText(String(fixtures.dashboard.todayCalls));
    await expect(metricValue(page, "平均耗时")).toHaveText("1.2 s");
    // Error rate is the rendered half of the pair C-21 defines; 1 - errorRate
    // is therefore 88.9%.
    await expect(metricValue(page, "错误率")).toHaveText("11.1%");
    expect(dashboard.metrics).toMatchObject({
      todayCalls: fixtures.dashboard.todayCalls,
      avgLatencyMs: fixtures.dashboard.avgLatencyMs,
    });
  });

  test("趋势图悬停显示该整点的数值与时间", async ({ page, playwright }) => {
    const fixtures = loadFixtures();
    const account = { email: fixtures.dashboard.accountEmail };
    const api = await openApiSession(playwright.request, account);
    const response = await api.get("/api/dashboard/admin");
    const body = (await response.json()) as { data: AdminDashboardPayload };
    const dashboard = body.data;
    await api.dispose();

    await openDashboard(page, account.email);
    await expect(page.getByRole("heading", { name: "24 小时调用趋势" })).toBeVisible();

    // Expected label → calls, read off the same API the chart renders from but
    // formatted by the browser's own clock, exactly like the axis.
    const expected = new Map<string, number>();
    for (const point of dashboard.trend) {
      expected.set(await bucketLabel(page, point.hour), point.calls);
    }
    const lastPoint = dashboard.trend[dashboard.trend.length - 1];
    expect(lastPoint.calls).toBe(fixtures.dashboard.todayCalls);

    const chart = page.locator(".recharts-wrapper").first();
    const box = await chart.boundingBox();
    if (box === null) throw new Error("trend chart has no layout box");
    const tooltip = page.locator(".recharts-tooltip-wrapper").first();

    // Sweep the plot in small steps. The wrapper includes the axis margins,
    // where no bucket is active, so positions without a tooltip are skipped
    // instead of assumed; every position that does show one must show the
    // bucket under the cursor, with that bucket's own call count.
    const samples: { label: string; value: number }[] = [];
    for (let percent = 2; percent <= 98; percent += 1) {
      const x = box.x + (box.width * percent) / 100;
      await page.mouse.move(x, box.y + box.height * 0.5);
      if (!(await tooltip.isVisible())) continue;
      const label = (await tooltip.locator(".recharts-tooltip-label").innerText()).trim();
      const value = Number(
        (await tooltip.locator(".recharts-tooltip-item-value").first().innerText()).trim(),
      );
      samples.push({ label, value });
    }

    expect(samples.length).toBeGreaterThan(15);
    for (const sample of samples) {
      expect(sample.label).toMatch(/^\d{2}:00$/);
      expect(expected.get(sample.label), sample.label).toBe(sample.value);
    }
    // Hovering across the chart tracks the pointer: several distinct hours are
    // reachable, the seeded current-hour bucket (9 calls in it) shows up, and a
    // sparse hour still renders its zero.
    expect(new Set(samples.map((sample) => sample.label)).size).toBeGreaterThanOrEqual(12);
    expect(samples.some((sample) => sample.value === fixtures.dashboard.todayCalls)).toBe(true);
    expect(samples.some((sample) => sample.value === 0)).toBe(true);
  });

  test("排行按今日调用量降序", async ({ page }) => {
    const fixtures = loadFixtures();
    await openDashboard(page, fixtures.dashboard.accountEmail);

    const card = page
      .getByRole("heading", { name: "Top 智能体" })
      .locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');
    const rows = card.locator("div.flex.items-center.gap-3");
    await expect(rows).toHaveCount(fixtures.dashboard.ranking.length);

    const rendered = await rows.allInnerTexts();
    fixtures.dashboard.ranking.forEach((entry, index) => {
      const text = rendered[index];
      expect(text, `row ${index + 1}`).toContain(String(index + 1));
      expect(text, `row ${index + 1}`).toContain(entry.name);
      expect(text, `row ${index + 1}`).toContain(`${entry.calls} 次`);
    });
  });
});
