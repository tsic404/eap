import { expect, test, type Page, type Response } from "@playwright/test";

import { loadFixtures, signIn, type FixtureManifest } from "../helpers/fixtures";

// TC-sec-01 (AC-42, 03-acceptance.md @ anchor dfa07d3:98): tenant A's agent and
// conversation lists show their own rows and never tenant B's, and a direct URL
// to tenant B's agent is indistinguishable from a missing agent — 404 with no
// field of the resource.
//
// Both sides are seeded by deploy/e2e/seed_fixtures.py and read from the run's
// manifest (`crossTenant`), so a renamed fixture reaches the assertion instead
// of passing against a stale literal.
export const TC_ID = "TC-sec-01";

interface CrossTenantTenant {
  email: string;
  agentId: string;
  agentName: string;
  conversationId: string;
  conversationTitle: string;
}

interface CrossTenantFixture {
  tenantA: CrossTenantTenant;
  tenantB: CrossTenantTenant & { agentDescription: string; difyAppId: string };
}

// The connector seeds this fixture set but the shared manifest type does not
// name every set; the shape is fixed by seed_fixtures.py's returned manifest.
const { crossTenant } = loadFixtures() as FixtureManifest & {
  crossTenant: CrossTenantFixture;
};
const { tenantA, tenantB } = crossTenant;

function pathOf(response: Response): string {
  return new URL(response.url()).pathname;
}

/** The `data.items` array of a list envelope; a malformed body fails loudly. */
function readItems(body: unknown): unknown[] {
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new Error("response body has no data envelope");
  }
  const { data } = body as { data: unknown };
  if (typeof data !== "object" || data === null || !("items" in data)) {
    throw new Error("response data has no items array");
  }
  const { items } = data as { items: unknown };
  if (!Array.isArray(items)) {
    throw new Error("response data.items is not an array");
  }
  return items;
}

function readErrorCode(body: unknown): string {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    throw new Error("error response has no error envelope");
  }
  const { error } = body as { error: unknown };
  if (typeof error !== "object" || error === null || !("code" in error)) {
    throw new Error("error response has no code");
  }
  const { code } = error as { code: unknown };
  if (typeof code !== "string") {
    throw new Error("error.code is not a string");
  }
  return code;
}

/** Navigate to an agent detail URL and return the authenticated 404 response. */
async function openAgentDetail(page: Page, agentId: string): Promise<Response> {
  // A fresh document loads before its in-memory access token exists, so the
  // first attempt can answer 401 and be replayed after the refresh channel.
  // The judgement is the replayed answer, so match only the terminal 404.
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "GET" &&
      pathOf(candidate) === `/api/agents/${agentId}` &&
      candidate.status() === 404,
    { timeout: 20_000 },
  );
  await page.goto(`/admin/agents/${agentId}`);
  return response;
}

test("tenant A lists show their own rows and no tenant B data", async ({ page }) => {
  await signIn(page, { email: tenantA.email });

  const agentsResponse = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "GET" &&
      pathOf(candidate) === "/api/agents" &&
      candidate.status() === 200,
  );
  await page.goto("/user/agents");
  const agents = await agentsResponse;
  expect(agents.status()).toBe(200);
  const agentsBody: unknown = await agents.json();
  expect(readItems(agentsBody).every((item) => typeof item === "object")).toBe(true);
  const agentsPayload = JSON.stringify(agentsBody);
  // Positive control first: tenant A's own row is listed...
  expect(agentsPayload).toContain(tenantA.agentId);
  await expect(page.getByText(tenantA.agentName)).toBeVisible();
  // ...and tenant B's row never is.
  expect(agentsPayload).not.toContain(tenantB.agentId);
  expect(agentsPayload).not.toContain(tenantB.agentName);
  await expect(page.getByText(tenantB.agentName)).toHaveCount(0);

  const conversationsResponse = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "GET" &&
      pathOf(candidate) === "/api/conversations" &&
      candidate.status() === 200,
  );
  await page.goto("/user/conversations");
  const conversations = await conversationsResponse;
  expect(conversations.status()).toBe(200);
  const conversationsBody: unknown = await conversations.json();
  expect(readItems(conversationsBody).every((item) => typeof item === "object")).toBe(true);
  const conversationsPayload = JSON.stringify(conversationsBody);
  expect(conversationsPayload).toContain(tenantA.conversationId);
  await expect(page.getByText(tenantA.conversationTitle)).toBeVisible();
  expect(conversationsPayload).not.toContain(tenantB.conversationId);
  expect(conversationsPayload).not.toContain(tenantB.conversationTitle);
  await expect(page.getByText(tenantB.conversationTitle)).toHaveCount(0);
});

test("cross-tenant agent detail is 404 and leaks no field", async ({ page }) => {
  await signIn(page, { email: tenantA.email });

  const crossTenant = await openAgentDetail(page, tenantB.agentId);
  expect(crossTenant.status()).toBe(404);
  const crossBody: unknown = await crossTenant.json();
  expect(readErrorCode(crossBody)).toBe("NOT_FOUND");

  const serialized = JSON.stringify(crossBody);
  for (const value of [
    tenantB.agentId,
    tenantB.agentName,
    tenantB.agentDescription,
    tenantB.difyAppId,
    tenantB.email,
  ]) {
    expect(serialized).not.toContain(value);
  }

  // The page settles on its failure state, renders no tenant B field and opens
  // no detail surface (the config/debug/log tabs only exist for a loaded agent).
  await expect(page.getByText("加载失败")).toBeVisible();
  await expect(page.getByText(tenantB.agentName)).toHaveCount(0);
  await expect(page.getByText(tenantB.agentDescription)).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);

  // An id that never existed answers exactly the same (C-14, C-109).
  const missing = await openAgentDetail(page, "never-registered-agent");
  expect(missing.status()).toBe(crossTenant.status());
  expect(await missing.json()).toEqual(crossBody);
});
