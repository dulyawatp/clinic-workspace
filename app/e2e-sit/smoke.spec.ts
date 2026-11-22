import { expect, test } from "@playwright/test";

const ORIGIN = "http://127.0.0.1:18081";
const PHYSICIAN = "sit.doc@example.invalid";

test("native SIT physician signs in and opens doctor route", async ({ page }) => {
  // This is an operator attestation, not an automatic site identity check.
  if (process.env.CLINIC_SIT_VERIFIED_SITE !== "marley-sit.local") {
    throw new Error("Verify site identity and installed apps, then set CLINIC_SIT_VERIFIED_SITE=marley-sit.local");
  }
  const user = process.env.CLINIC_SIT_PHYSICIAN_USER;
  const password = process.env.CLINIC_SIT_PHYSICIAN_PASSWORD;
  if (user !== PHYSICIAN || !password) {
    throw new Error("Provide the provisioned synthetic SIT physician credentials");
  }

  let blocked = false;
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (
      url.origin === ORIGIN &&
      (["GET", "HEAD", "OPTIONS"].includes(method) ||
        (method === "POST" && url.pathname === "/api/method/login"))
    ) {
      await route.continue();
    } else {
      blocked = true;
      await route.abort();
    }
  });

  // Do not expose Playwright action logs containing credential values.
  try {
    await page.goto("/clinic/login");
    await page.locator('input[name="usr"]').fill(user);
    await page.locator('input[name="pwd"]').fill(password);
    await page.locator('button[type="submit"]').click();
    await page.locator("header.shell-topbar").waitFor({ state: "visible" });
  } catch {
    throw new Error("Native physician sign-in failed");
  }

  const bootstrap = await page.evaluate(async () => {
    const response = await fetch("/api/method/clinic_ui.api.session.bootstrap");
    if (!response.ok) return false;
    const data = await response.json();
    return data.message?.mode === "live" && data.message?.user?.roles?.includes("DOC") === true;
  });
  expect(bootstrap, "native bootstrap must identify a live physician session").toBe(true);
  await page.goto("/clinic/doctor");
  await expect(page).toHaveURL(`${ORIGIN}/clinic/doctor`);
  await expect(page.locator("header.shell-topbar")).toBeVisible();
  expect(blocked, "cross-origin or non-login write request was blocked").toBe(false);
});
