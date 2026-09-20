import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { loginViaSso } from "./helpers/api";

/**
 * axe-core gate (§30.9): no critical or serious violations on any P0 page.
 * "Serious" is the highest WCAG-failure band axe reports for contrast, dialog
 * naming, landmark structure, etc.; critical covers the blocking cases above it.
 */
async function seriousViolations(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze();
  return violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
}

function describeViolations(violations: unknown[]): string {
  return JSON.stringify(
    violations.map((v) => ({
      id: (v as { id: string }).id,
      impact: (v as { impact: string }).impact,
      help: (v as { help: string }).help,
      nodes: (v as { nodes: { target: string[] }[] }).nodes.length,
    })),
    null,
    2,
  );
}

test("public pages have no serious axe violations", async ({ page }) => {
  await page.goto("/login");
  expect(await seriousViolations(page), "login page").toEqual([]);

  await page.goto("/forbidden");
  expect(await seriousViolations(page), "forbidden page").toEqual([]);
});

test("authenticated user pages have no serious axe violations", async ({ page }) => {
  await loginViaSso(page);

  for (const path of ["/user", "/user/agents", "/user/conversations", "/user/tasks"]) {
    await page.goto(path);
    const violations = await seriousViolations(page);
    expect(violations, `axe violations on ${path}:\n${describeViolations(violations)}`).toEqual([]);
  }
});
