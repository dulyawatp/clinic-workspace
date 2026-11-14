import { expect } from "@playwright/test";
import { loginAs, test } from "./helpers";

for (const width of [390, 834, 1440]) {
  test(`visit identity stays below topbar at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await loginAs(page, "DOC");
    await page.goto("/clinic/visits/ENC-1");
    await expect(page.getByTestId("encounter-id")).toHaveText("ENC-1");
    // Future section content is not implemented yet. Give the real workspace
    // enough height to exercise both sticky elements during scrolling.
    await page.locator(".visit-workspace").evaluate((workspace) => {
      const spacer = document.createElement("div");
      spacer.style.height = "1600px";
      spacer.setAttribute("aria-hidden", "true");
      workspace.appendChild(spacer);
    });
    await page.evaluate(() => window.scrollTo(0, 700));
    const aligned = async () =>
      page.evaluate(() => {
        const topbar = document
          .querySelector(".shell-topbar")
          ?.getBoundingClientRect();
        const identity = document
          .querySelector(".visit-identity")
          ?.getBoundingClientRect();
        if (!topbar || !identity) return false;
        return (
          Math.abs(identity.top - topbar.bottom) < 2 &&
          identity.bottom <= window.innerHeight
        );
      });
    await expect.poll(aligned).toBe(true);
    await expect(page.getByTestId("encounter-id")).toBeVisible();
    await page.getByRole("button", { name: "EN", exact: true }).click();
    await expect.poll(aligned).toBe(true);
  });
}
