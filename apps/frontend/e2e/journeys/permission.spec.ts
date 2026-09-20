import { expect, test } from "@playwright/test";

// P0 journey §31.2.2: an employee who reaches the admin workspace is served the
// friendly permission notice at the original `/admin` URL (middleware rewrite),
// never the admin content.
test("employee sees the permission notice on /admin", async ({ page }) => {
  await page.goto("/admin");

  // The URL stays /admin; the body is the /forbidden rewrite.
  await expect(page).toHaveURL(/\/admin/);
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
  await expect(page.getByText(/agent_admin/)).toBeVisible();
  await expect(page.getByRole("link", { name: "返回用户工作区" })).toBeVisible();
});
