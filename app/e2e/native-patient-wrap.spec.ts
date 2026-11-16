import { expect, test } from "@playwright/test";

const alias = `workspace-${"a".repeat(120)}`;

test.skip(process.env.VITE_CLINIC_NATIVE !== "1", "Requires native transport in the Vite server.");

for (const width of [390, 834, 1440]) {
  test(`native patient reference wraps without page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/method/clinic_ui.api.session.bootstrap", (route) => route.fulfill({
      json: { message: { mode: "live", sessionEpoch: "epoch", user: { id: "u", displayName: "User", roles: ["DOC"] }, capabilities: { "read.patient": "enabled" }, csrfToken: "csrf" } },
    }));
    await page.route("**/api/method/clinic_ui.api.patients.view?*", (route) => route.fulfill({
      json: { message: { workspace_patient_id: alias, display_name: "Synthetic Patient", first_name: "Synthetic", last_name: "Patient", sex: "Other", dob: "2000-01-01", status: "Active", modified: "version" } },
    }));
    await page.goto(`/clinic/patients/${alias}`);
    const reference = page.getByTestId("patient-id");
    await expect(reference).toHaveText(alias);
    await expect(reference).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await reference.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth;
    })).toBe(true);
  });
}
