import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";
import { ROUTES } from "../src/app/routes";
import { LOCALE_KEY } from "../src/i18n/locale-storage";
import { loginAs, test } from "./helpers";

/** Fails with the violation list, so a regression never passes silently. */
async function expectNoSeriousViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((violation) => {
    return violation.impact === "serious";
  });
  expect(serious).toEqual([]);
}

test("login screen has no serious axe violations at 1440", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(ROUTES.login);
  await expectNoSeriousViolations(page);
});

for (const { locale, eyebrow, signedInAs, role, receptionist } of [
  {
    locale: "th",
    eyebrow: "เดโม คลินิก · ข้อมูลตัวอย่าง",
    signedInAs: "เข้าสู่ระบบในชื่อ",
    role: "บทบาท",
    receptionist: "เจ้าหน้าที่ต้อนรับ",
  },
  {
    locale: "en",
    eyebrow: "Demo Clinic · Demo data",
    signedInAs: "Signed in as",
    role: "Role",
    receptionist: "Receptionist",
  },
]) {
  test(`REC Today has no serious axe violations at 1440 (${locale})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(
      ({ key, value }) => localStorage.setItem(key, value),
      { key: LOCALE_KEY, value: locale },
    );
    await loginAs(page, "REC");
    await expect(page).toHaveURL(ROUTES.home);
    const home = page.getByTestId("role-home");
    await expect(home.getByText(eyebrow, { exact: true })).toBeVisible();
    const facts = home.locator(".role-home__facts");
    await expect(facts.getByText(signedInAs, { exact: true })).toBeVisible();
    await expect(facts.getByText(role, { exact: true })).toBeVisible();
    await expect(facts.getByText(receptionist, { exact: true })).toBeVisible();
    await expectNoSeriousViolations(page);
  });
}

test("authenticated search has no serious axe violations at 390", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto(ROUTES.search);
  await page.locator('input[type="search"]').waitFor({ state: "visible" });
  await expectNoSeriousViolations(page);
});

test("authenticated search has no serious axe violations at 834", async ({
  page,
}) => {
  await page.setViewportSize({ width: 834, height: 1000 });
  await loginAs(page, "REC");
  await page.goto(ROUTES.search);
  await page.locator('input[type="search"]').waitFor({ state: "visible" });
  await expectNoSeriousViolations(page);
});

test("drawer dialog has no serious axe violations at 834", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1000 });
  await loginAs(page, "REC");
  await page.getByRole("button", { name: /menu|เมนู/i }).click();
  // Scan only after the modal dialog is actually mounted and shown.
  await expect(page.locator("#shell-drawer")).toBeVisible();
  await expectNoSeriousViolations(page);
});
