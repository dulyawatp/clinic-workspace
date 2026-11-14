import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";
import { ROUTES } from "../src/app/routes";
import { loginAs, test } from "./helpers";

for (const width of [390, 834, 1440]) {
  test(`reception and queue remain usable at ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await loginAs(page, "REC");
    await page.goto(ROUTES.reception);
    await expect(page.getByTestId("kpi-todayAppointments")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.goto(ROUTES.queue);
    await expect(
      page.getByRole("button", { name: /รับเข้าตรวจ|check in/i }),
    ).toBeDisabled();
    await expect(page.getByRole("table")).toBeVisible();
    if (width === 1440) {
      await expect(
        page.locator('.shell-nav--sidebar a[aria-current="page"]'),
      ).toHaveCount(1);
    }
    if (width === 834) {
      const link = page
        .getByRole("link", { name: /ดูรายละเอียดนัดหมาย|view appointment/i })
        .first();
      const box = await link.boundingBox();
      expect(box).not.toBeNull();
      expect((box?.x ?? width) + (box?.width ?? width)).toBeLessThanOrEqual(
        width,
      );
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`queue-${width}.png`),
      fullPage: true,
    });
  });
}

test("mobile queue exposes table headers, no axe violations and keeps filter on locale change", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto(ROUTES.queue);
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader")).toHaveCount(7);
  await expect(page.getByRole("button", { name: /รับเข้าตรวจ/i })).toBeVisible();
  expect(
    await page
      .getByRole("button", { name: /รับเข้าตรวจ/i })
      .evaluate(
        (node) =>
          node.scrollHeight <= node.clientHeight &&
          node.scrollWidth <= node.clientWidth,
      ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.goto(`${ROUTES.queue}?status=waiting`);
  await expect(
    page.getByRole("combobox", { name: "สถานะ", exact: true }),
  ).toHaveValue("waiting");
  await expect(page.getByText("Narin Chaiyaporn")).toBeVisible();
  await expect(page.getByText("Daniel Okafor")).toHaveCount(0);
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Status", exact: true }),
  ).toHaveValue("waiting");
  await expect(page.getByText("Narin Chaiyaporn")).toBeVisible();
  await expect(page).toHaveURL(/\?status=waiting$/);
  await expect(page.getByText("Daniel Okafor")).toHaveCount(0);
  await page.getByRole("searchbox").fill("private-search");
  await page
    .getByRole("combobox", { name: "Status", exact: true })
    .selectOption("arrived");
  await expect(page).toHaveURL(/\?status=arrived$/);
  await page.goto(`${ROUTES.queue}?status=unknown&q=private-search`);
  await expect(page).toHaveURL(/\/clinic\/reception\/queue$/);
  await expect(page.getByText("Daniel Okafor")).toBeVisible();
});

test("calendar slot opens the correct read-only appointment", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto(ROUTES.appointments);
  await page
    .getByRole("button", { name: /2026-11-05 Fixture Doctor 09:30/ })
    .click();
  await page
    .getByRole("link", { name: /ดูรายละเอียดนัดหมาย|view appointment/i })
    .click();
  await expect(page).toHaveURL(/fixture-apt-02/);
  await expect(page.getByText("fixture-dir-02")).toBeVisible();
  await expect(page.getByLabel(/วันที่|date/i)).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
