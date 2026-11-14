import { createHash } from "node:crypto";
import { expect } from "@playwright/test";
import { loginAs, test } from "./helpers";

test("fixture gallery and comparison at three widths in Thai and English", async ({
  page,
}) => {
  await loginAs(page, "DOC");
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/") && request.method() !== "GET")
      mutations.push(request.method());
  });
  await page.goto("/clinic/visits/ENC-1");
  const link = page.getByRole("link", { name: "ภาพถ่ายผู้ป่วย" });
  await expect(link).toHaveAttribute("href", "/clinic/patients/PAT-1/photos");
  await link.click();
  await expect(page.getByText("ภาพต้นฉบับของผู้ป่วย")).toBeVisible();

  for (const width of [390, 834, 1440]) {
    for (const language of ["TH", "EN"]) {
      await page.setViewportSize({ width, height: 900 });
      const imageResponse = page.waitForResponse((response) =>
        response.url().endsWith("/api/files/fixture-photo-1"),
      );
      await page.goto("/clinic/patients/PAT-1/photos");
      await page.getByRole("button", { name: language, exact: true }).click();
      await expect(
        page.getByText(
          language === "EN" ? "Patient originals" : "ภาพต้นฉบับของผู้ป่วย",
        ),
      ).toBeVisible();
      await expect(
        page.locator('img[src="/api/files/fixture-photo-1"]'),
      ).toHaveJSProperty("naturalWidth", 48);
      const image = await imageResponse;
      expect(image.status()).toBe(200);
      expect(image.headers()["cache-control"]).toBe("no-store");
      expect(
        createHash("sha256")
          .update(await image.body())
          .digest("hex"),
      ).toBe(
        "7eae15f049f13532aea3c50a7cf98e3b7a78f510cfa43cc1665c4d74754d63d3",
      );
      await expect(
        page.getByText(
          language === "EN" ? /Obtain recorded consent/ : /ขั้นตอนที่คลินิกอนุญาต/,
        ),
      ).toBeVisible();
      await page
        .getByRole("link", {
          name: language === "EN" ? "Compare photos" : "เปรียบเทียบภาพถ่าย",
        })
        .click();
      await expect(
        page.getByText(language === "EN" ? "Side by side" : "เปรียบเทียบเคียงข้าง"),
      ).toBeVisible();
      const range = page.locator('input[type="range"]');
      await range.focus();
      await page.keyboard.press("ArrowRight");
      await expect(range).toHaveValue("51");
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    }
  }
  expect(mutations).toEqual([]);
});
