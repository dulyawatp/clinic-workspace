import { expect } from "@playwright/test";
import { loginAs, test } from "./helpers";

test("nav becomes a drawer at 834", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1000 });
  await loginAs(page, "REC");
  await expect(page.getByRole("button", { name: /menu|เมนู/i })).toBeVisible();
});

test("notification label does not overflow at 390", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.getByRole("button", { name: /notifications|การแจ้งเตือน/i }).click();
  // The label under test lives in the popover; a closed popover proves nothing.
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(
    await page.evaluate(() => {
      return document.documentElement.scrollWidth <= window.innerWidth;
    }),
  ).toBe(true);
});

test("no horizontal scroll at 390 on search", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto("/clinic/search");
  await page.locator('input[type="search"]').waitFor({ state: "visible" });
  expect(
    await page.evaluate(() => {
      return document.documentElement.scrollWidth <= window.innerWidth;
    }),
  ).toBe(true);
});
