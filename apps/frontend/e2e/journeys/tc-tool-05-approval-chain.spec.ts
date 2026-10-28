import { setTimeout as sleep } from "node:timers/promises";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import {
  cardByHeading,
  fetchTask,
  loadFixtures,
  openApiSession,
  resetStubCalls,
  signIn,
  STACK_ORIGIN,
  stubCalls,
} from "../helpers/fixtures";

// Browser-side evidence for the approval chain. The stack's executor consumes
// the queue in the backend process, so approving a task here runs the real
// worker against the stub upstream; nothing is replayed. The approvable task's
// upstream answers after a delay, which keeps it in `executing` long enough for
// the API poll below to observe the middle state rather than infer it.

const TC_ID = "TC-tool-05";
// The only states a tool-approval task may pass through on the happy path,
// in this order (the chain the judgement names).
const APPROVAL_CHAIN: readonly string[] = ["pending", "approved", "executing", "completed"];
const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const POLL_INTERVAL_MS = 120;
const TERMINAL_TIMEOUT_MS = 40_000;

/** Record a task's status after each change until it reaches a terminal state. */
async function watchTask(taskId: string, api: APIRequestContext, firstStatus: string) {
  const observed = [firstStatus];
  const deadline = Date.now() + TERMINAL_TIMEOUT_MS;
  let latest = await fetchTask(api, taskId);
  while (Date.now() < deadline) {
    if (latest.status !== observed[observed.length - 1]) observed.push(latest.status);
    if (TERMINAL.has(latest.status)) break;
    await sleep(POLL_INTERVAL_MS);
    latest = await fetchTask(api, taskId);
  }
  return { observed, latest };
}

/** Open the task center as `account` in its own browser context. */
async function taskCenter(page: Page, account: { email: string }) {
  await signIn(page, account);
  await page.goto("/user/tasks");
  await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible();
}

test.describe(TC_ID, () => {
  test("同意后严格按待审批→已通过→执行中→已完成流转，员工收到结果", async ({
    page,
    playwright,
    browser,
  }) => {
    const fixtures = loadFixtures();
    const task = fixtures.tasks.approve;
    expect(task.seededStatus).toBe("pending");
    await resetStubCalls(playwright.request, fixtures.stub.publicUrl);

    const api = await openApiSession(playwright.request, LOCAL_ACCOUNTS.admin);
    await taskCenter(page, LOCAL_ACCOUNTS.admin);

    const card = cardByHeading(page, task.title);
    await expect(card.getByText("待审批", { exact: true })).toBeVisible();
    const seeded = await fetchTask(api, task.id);
    expect(seeded.status).toBe("pending");

    await card.getByRole("button", { name: "同意" }).click();
    await expect(page.getByRole("status").getByText("已通过")).toBeVisible();

    const { observed, latest } = await watchTask(task.id, api, seeded.status);
    expect(latest.status).toBe("completed");
    // Strict ordering: every observed state is on the chain and none goes
    // backwards; the middle state was seen, so the run did not jump.
    const positions = observed.map((status) => APPROVAL_CHAIN.indexOf(status));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((left, right) => left - right));
    expect(observed).toEqual(expect.arrayContaining(["pending", "executing", "completed"]));

    // The result block carries the upstream's answer, which only a real call
    // could produce; the ledger proves it ran exactly once.
    expect(latest.result).toMatchObject({ status_code: 200 });
    expect(JSON.stringify(latest.result)).toContain("tool-stub executed");
    const calls = await stubCalls(playwright.request, fixtures.stub.publicUrl, fixtures.stub.approveMarker);
    expect(calls).toHaveLength(1);
    expect(calls[0].delayMs).toBe(fixtures.stub.approveDelayMs);

    await page.reload();
    const settledCard = cardByHeading(page, task.title);
    await expect(settledCard.getByText("已完成", { exact: true })).toBeVisible();
    await expect(settledCard.getByText("执行结果")).toBeVisible();
    await expect(settledCard.getByText("200", { exact: true })).toBeVisible();
    await expect(settledCard.getByText(/耗时 \d+ ms/)).toBeVisible();
    await expect(settledCard.locator("pre")).toContainText("tool-stub executed");

    // The requesting employee sees the same outcome, signed in with their own
    // seeded account rather than a role cookie.
    const employeeContext = await browser.newContext({ baseURL: STACK_ORIGIN });
    const employeePage = await employeeContext.newPage();
    await taskCenter(employeePage, LOCAL_ACCOUNTS.employee);
    const employeeCard = cardByHeading(employeePage, task.title);
    await expect(employeeCard.getByText("已完成", { exact: true })).toBeVisible();
    await expect(employeeCard.getByText("执行结果")).toBeVisible();
    await expect(employeeCard.getByText("200", { exact: true })).toBeVisible();
    await expect(employeeCard.locator("pre")).toContainText("tool-stub executed");
    await employeeContext.close();
    await api.dispose();
  });

  test("拒绝后员工看到「工具被拒绝」且工具不执行", async ({ page, playwright, browser }) => {
    const fixtures = loadFixtures();
    const task = fixtures.tasks.reject;
    expect(task.seededStatus).toBe("pending");
    await resetStubCalls(playwright.request, fixtures.stub.publicUrl);

    const api = await openApiSession(playwright.request, LOCAL_ACCOUNTS.admin);
    await taskCenter(page, LOCAL_ACCOUNTS.admin);

    const card = cardByHeading(page, task.title);
    await expect(card.getByText("待审批", { exact: true })).toBeVisible();
    await card.getByRole("button", { name: "拒绝" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "拒绝审批" })).toBeVisible();
    await dialog.getByLabel("拒绝原因").fill("风险过高，不予放行");
    await dialog.getByRole("button", { name: "确认拒绝" }).click();
    await expect(page.getByRole("status").getByText("已拒绝")).toBeVisible();

    await page.reload();
    const rejectedCard = cardByHeading(page, task.title);
    await expect(rejectedCard.getByText("已拒绝", { exact: true })).toBeVisible();
    await expect(rejectedCard.getByText("工具被拒绝")).toBeVisible();
    await expect(rejectedCard.getByText("驳回原因：风险过高，不予放行")).toBeVisible();

    const rejected = await fetchTask(api, task.id);
    expect(rejected.status).toBe("rejected");
    expect(rejected.result).toMatchObject({ reason: "风险过高，不予放行" });
    // A rejection is a decision, never an execution.
    const calls = await stubCalls(playwright.request, fixtures.stub.publicUrl, fixtures.stub.deniedMarker);
    expect(calls).toHaveLength(0);

    const employeeContext = await browser.newContext({ baseURL: STACK_ORIGIN });
    const employeePage = await employeeContext.newPage();
    await taskCenter(employeePage, LOCAL_ACCOUNTS.employee);
    const employeeCard = cardByHeading(employeePage, task.title);
    await expect(employeeCard.getByText("工具被拒绝")).toBeVisible();
    await expect(employeeCard.getByText("驳回原因：风险过高，不予放行")).toBeVisible();
    await employeeContext.close();
    await api.dispose();
  });

  test("24 小时超时后任务被取消，界面给出超时说明", async ({ page, playwright }) => {
    const fixtures = loadFixtures();
    const task = fixtures.tasks.timeout;
    // The row was inserted still `pending` with a deadline one day in the past:
    // the 15s expiry scan, not the fixture, is what has to retire it.
    expect(task.seededStatus).toBe("pending");

    const api = await openApiSession(playwright.request, LOCAL_ACCOUNTS.admin);
    const deadline = Date.now() + TERMINAL_TIMEOUT_MS;
    let latest = await fetchTask(api, task.id);
    while (latest.status === "pending" && Date.now() < deadline) {
      await sleep(250);
      latest = await fetchTask(api, task.id);
    }
    expect(latest.status).toBe("cancelled");
    expect(latest.result).toMatchObject({ reason: "超时取消" });

    await taskCenter(page, LOCAL_ACCOUNTS.admin);
    const card = cardByHeading(page, task.title);
    await expect(card.getByText("已取消", { exact: true })).toBeVisible();
    await expect(card.getByText("审批超过 24 小时未处理，任务已取消")).toBeVisible();
    // A retired approval offers no way forward.
    await expect(card.getByRole("button")).toHaveCount(0);
    await api.dispose();
  });
});
