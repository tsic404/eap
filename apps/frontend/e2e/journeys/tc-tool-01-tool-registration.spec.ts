import { expect, test, type Locator, type Page } from "@playwright/test";

import { LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { openApiSession, signIn } from "../helpers/fixtures";

// Browser-side evidence for the tool registration form: the judgement is that a
// type is chosen from five cards, the risk slider carries three coloured bands,
// the permission mode is a mutually exclusive single choice, and a save adds the
// list badge. The tool create endpoint is the platform's own, so the created row
// is read back through the API instead of a replayed response.

const TC_ID = "TC-tool-01";
const TYPE_LABELS = ["HTTP", "数据库", "RPA", "Webhook", "MCP"] as const;
const RISK_LABELS = ["低风险", "中风险", "高风险"] as const;
const PERMISSION_LABELS = ["自动执行", "需确认", "已禁用"] as const;

const TOOL_ID = "e2e-ui-tool";
const TOOL_NAME = "探针注册工具";

// Only the selected card carries the tinted background; `border-primary`
// alone would also match the idle card's `hover:border-primary` utility.
const SELECTED_CARD_CLASS = /(^|\s)bg-primary-subtle(\s|$)/;

function rgbParts(color: string): [number, number, number] {
  const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (match === null) throw new Error(`unexpected colour: ${color}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

/** Hue of a colour, so a band can be judged green / amber / red by family. */
function colourFamily(color: string): "green" | "amber" | "red" {
  const [r, g, b] = rgbParts(color);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) throw new Error(`achromatic band: ${color}`);
  const spread = max - min;
  let hue: number;
  if (max === r) hue = 60 * (((g - b) / spread) % 6);
  else if (max === g) hue = 60 * ((b - r) / spread + 2);
  else hue = 60 * ((r - g) / spread + 4);
  if (hue < 0) hue += 360;
  if (hue < 15 || hue >= 345) return "red";
  if (hue < 70) return "amber";
  return "green";
}

/** The card wrapping one radio: the card is the clickable label. */
function optionCard(group: Locator, name: string): Locator {
  return group.getByRole("radio", { name }).locator("xpath=ancestor::label[1]");
}

/** Pick a card the way a user does — by clicking the visible card. */
async function chooseCard(group: Locator, name: string, labels: readonly string[]) {
  await optionCard(group, name).click();
  await expect(group.getByRole("radio", { name })).toBeChecked();
  await expect(optionCard(group, name)).toHaveClass(SELECTED_CARD_CLASS);
  for (const other of labels.filter((label) => label !== name)) {
    await expect(group.getByRole("radio", { name: other })).not.toBeChecked();
    await expect(optionCard(group, other)).not.toHaveClass(SELECTED_CARD_CLASS);
  }
}

async function openForm(page: Page) {
  await signIn(page, LOCAL_ACCOUNTS.admin);
  await page.goto("/admin/tools/new");
  await expect(page.getByRole("heading", { name: "新建工具" })).toBeVisible();
}

test.describe(TC_ID, () => {
  test("五种类型卡片、三段风险色带与互斥的权限模式", async ({ page }) => {
    await openForm(page);

    const typeGroup = page.getByRole("radiogroup", { name: "类型" });
    await expect(typeGroup.getByRole("radio")).toHaveCount(TYPE_LABELS.length);
    for (const label of TYPE_LABELS) {
      await chooseCard(typeGroup, label, TYPE_LABELS);
    }

    const slider = page.getByRole("slider", { name: "风险等级" });
    await expect(slider).toHaveValue("1");
    await expect(slider).toHaveAttribute("aria-valuetext", RISK_LABELS[1]);
    await slider.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(slider).toHaveAttribute("aria-valuetext", RISK_LABELS[0]);
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(slider).toHaveAttribute("aria-valuetext", RISK_LABELS[2]);

    // The three bands below the slider carry the product's low/medium/high
    // colours. Comparing them with freshly resolved semantic utility classes
    // asserts the mapping (success/warning/danger) rather than a copied hex.
    const colours = await page.evaluate(() => {
      const probe = (className: string) => {
        const element = document.createElement("span");
        element.className = className;
        element.style.display = "none";
        document.body.appendChild(element);
        const colour = getComputedStyle(element).backgroundColor;
        element.remove();
        return colour;
      };
      return {
        segments: Array.from(document.querySelectorAll("[data-risk-stop]")).map(
          (element) => getComputedStyle(element).backgroundColor,
        ),
        success: probe("bg-success"),
        warning: probe("bg-warning"),
        danger: probe("bg-danger"),
      };
    });
    await expect(page.locator("[data-risk-stop]")).toHaveCount(3);
    expect(colours.segments).toEqual([colours.success, colours.warning, colours.danger]);
    expect(new Set(colours.segments).size).toBe(3);
    expect(colours.segments.map(colourFamily)).toEqual(["green", "amber", "red"]);

    const permissionGroup = page.getByRole("radiogroup", { name: "权限模式" });
    await expect(permissionGroup.getByRole("radio")).toHaveCount(PERMISSION_LABELS.length);
    await expect(permissionGroup.getByRole("radio", { name: PERMISSION_LABELS[0] })).toBeChecked();
    for (const label of PERMISSION_LABELS) {
      await chooseCard(permissionGroup, label, PERMISSION_LABELS);
    }
  });

  test("保存成功后列表新增带类型、风险与权限徽章的卡片", async ({ page, playwright }) => {
    await openForm(page);

    await page.getByLabel("名称").fill(TOOL_NAME);
    await page.getByLabel("标识（tool_id）").fill(TOOL_ID);
    await page.getByLabel("端点（Endpoint）").fill("https://api.example.com/v1/probe");
    const typeGroup = page.getByRole("radiogroup", { name: "类型" });
    await chooseCard(typeGroup, "数据库", TYPE_LABELS);
    const slider = page.getByRole("slider", { name: "风险等级" });
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect(slider).toHaveAttribute("aria-valuetext", "高风险");
    await chooseCard(
      page.getByRole("radiogroup", { name: "权限模式" }),
      "需确认",
      PERMISSION_LABELS,
    );

    await page.getByRole("button", { name: "创建" }).click();
    await expect(page.getByRole("status").getByText("工具已创建")).toBeVisible();
    await page.waitForURL(`**/admin/tools/${TOOL_ID}`);
    await expect(page.getByRole("heading", { name: TOOL_NAME })).toBeVisible();

    // The list is a separate surface from the detail header: the new row must
    // carry the name, the type + identifier line and both configuration badges.
    await page.goto("/admin/tools");
    const list = page.getByRole("list", { name: "工具列表" });
    const card = list.getByRole("listitem").filter({ hasText: TOOL_NAME });
    await expect(card).toHaveCount(1);
    await expect(card.getByText(`数据库 · ${TOOL_ID}`)).toBeVisible();
    await expect(card.getByText("高风险", { exact: true })).toBeVisible();
    await expect(card.getByText("需确认", { exact: true })).toBeVisible();
    await expect(card.getByText("启用", { exact: true })).toBeVisible();

    // The persisted row, read through the platform API: the form's three
    // choices reached storage instead of only the rendered card.
    const api = await openApiSession(playwright.request, LOCAL_ACCOUNTS.admin);
    const response = await api.get(`/api/tools/${TOOL_ID}`);
    expect(response.status()).toBe(200);
    const created = (await response.json()) as {
      data: { name: string; type: string; risk_level: string; permission_mode: string };
    };
    expect(created.data).toMatchObject({
      name: TOOL_NAME,
      type: "database",
      risk_level: "high",
      permission_mode: "confirm",
    });
    await api.dispose();
  });
});
