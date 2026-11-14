import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";
import { ROUTES } from "../src/app/routes";
import { loginAs, test } from "./helpers";

for (const width of [390, 834, 1440]) {
  test(`patient directory opens the exact profile at ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await loginAs(page, "REC");
    await page.goto(ROUTES.patients);
    const row = page.locator("tr").filter({ hasText: "สมชาย ใจดี" });
    await expect(row).toBeVisible();
    if (width === 390) {
      const table = page.locator(".patients-table");
      await expect(table.getByRole("columnheader")).toHaveCount(4);
      expect(
        (await new AxeBuilder({ page }).include(".patients-table").analyze())
          .violations,
      ).toEqual([]);
    }
    await page.screenshot({
      path: testInfo.outputPath(`patients-${width}.png`),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await row.getByRole("link").click();
    await expect(page).toHaveURL(/fixture-dir-01/);
    await expect(page.getByTestId("patient-id")).toHaveText("fixture-dir-01");
    await expect(page.getByText(/38\.2\s*°C/)).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}
