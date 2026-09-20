import { expect, test } from "@playwright/test";

import { loginViaSso } from "../helpers/api";

// P0 journey §31.2.8: losing connectivity surfaces the fixed offline banner,
// and reconnecting flashes the short "recovered" state.
test("network disconnect surfaces the offline banner", async ({ page, context }) => {
  await loginViaSso(page);

  await context.setOffline(true);
  await expect(page.getByText(/网络连接已断开/)).toBeVisible();

  await context.setOffline(false);
  await expect(page.getByText("已恢复连接")).toBeVisible();
});
