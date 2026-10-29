import { expect, test } from "@playwright/test";

import { LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import {
  cardByHeading,
  fetchTask,
  loadFixtures,
  openApiSession,
  signIn,
  stubCalls,
} from "../helpers/fixtures";

// Browser-side evidence for the negative half of the task state machine. The
// full 7×4 matrix is the backend acceptance suite's job; this journey asserts
// what that layer cannot see: a rejected/cancelled task offers no execution
// entry in the UI, and an out-of-band illegal request is refused at the
// endpoint without moving the row or the rendered state.

const TC_ID = "TC-tool-07";

const ILLEGAL_ACTIONS = [
  { path: "approve", data: {} },
  { path: "reject", data: { reason: "不应生效" } },
  { path: "retry", data: undefined },
  { path: "cancel", data: undefined },
] as const;

test.describe(TC_ID, () => {
  test("已驳回与已取消任务没有执行入口，非法请求 422 且界面状态不变", async ({
    page,
    playwright,
  }) => {
    const fixtures = loadFixtures();
    await signIn(page, LOCAL_ACCOUNTS.admin);
    await page.goto("/user/tasks");
    await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible();

    for (const [key, label] of [
      ["rejected", "已拒绝"],
      ["cancelled", "已取消"],
    ] as const) {
      const card = cardByHeading(page, fixtures.tasks[key].title);
      await expect(card.getByText(label, { exact: true })).toBeVisible();
      // Approve / reject / retry are the only user-driven edges; a terminal row
      // is offered none of them, and the card carries no other action control.
      await expect(card.getByRole("button")).toHaveCount(0);
    }

    const api = await openApiSession(playwright.request, LOCAL_ACCOUNTS.admin);
    for (const key of ["rejected", "cancelled"] as const) {
      const task = fixtures.tasks[key];
      const before = await fetchTask(api, task.id);
      expect(before.status).toBe(key);

      for (const action of ILLEGAL_ACTIONS) {
        const response = await api.post(`/api/tasks/${task.id}/${action.path}`, {
          data: action.data,
        });
        const body = (await response.json()) as { error?: { code?: string } };
        expect(
          { status: response.status(), code: body.error?.code },
          `${task.title} ${action.path}`,
        ).toEqual({ status: 422, code: "INVALID_TRANSITION" });
      }

      // The refusals left the row exactly where it was.
      const after = await fetchTask(api, task.id);
      expect(after.status).toBe(before.status);
      expect(after.result).toEqual(before.result);
    }
    await api.dispose();

    // Neither terminal row ever reached the executor: the upstream shared by
    // the rejected/cancelled fixtures recorded no call at all.
    const calls = await stubCalls(playwright.request, fixtures.stub.publicUrl, fixtures.stub.deniedMarker);
    expect(calls).toHaveLength(0);

    // Re-read the page after the refused requests: what is rendered is the
    // row's state, unchanged.
    await page.reload();
    const rejectedCard = cardByHeading(page, fixtures.tasks.rejected.title);
    await expect(rejectedCard.getByText("已拒绝", { exact: true })).toBeVisible();
    await expect(rejectedCard.getByRole("button")).toHaveCount(0);
    const cancelledCard = cardByHeading(page, fixtures.tasks.cancelled.title);
    await expect(cancelledCard.getByText("已取消", { exact: true })).toBeVisible();
    await expect(cancelledCard.getByRole("button")).toHaveCount(0);
  });
});
