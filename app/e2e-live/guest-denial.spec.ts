import { expect, test } from "@playwright/test";
import { MISSING_SECONDARY_REASON, secondaryCredential } from "./credentials";
import { guardWrites, signIn, TH } from "./helpers";

/** No credential is needed for the anonymous surface. */
test("guest is denied the live lab surface and live data", async ({ page }) => {
  const guard = await guardWrites(page);

  await page.goto("/clinic/lab/photos");
  await expect(page).toHaveURL(/\/clinic\/login(\?|$)/);
  await expect(
    page.getByRole("heading", { name: TH.loginTitle }),
  ).toBeVisible();
  await expect(page.locator(".photos-grid")).toHaveCount(0);
  await expect(page.locator("header.shell-topbar")).toHaveCount(0);

  // The proxy, not just the UI, refuses the live read surface.
  const livePatients = await page.request.get("/api/live/synthetic-patients");
  expect(livePatients.status()).toBe(401);
  const session = await page.request.get("/api/session");
  expect(session.status()).toBe(401);

  guard.assertNone();
});

/**
 * Non-clinician coverage is only claimed when a second private credential is
 * supplied. Without it the test is skipped with an explicit reason; it never
 * passes silently and never reports the missing case as covered.
 */
test("non-clinician credential cannot establish a live clinician session", async ({
  page,
}) => {
  const secondary = secondaryCredential();
  test.skip(secondary === null, MISSING_SECONDARY_REASON);
  if (secondary === null) return;

  const guard = await guardWrites(page);

  await signIn(page, secondary, { expectRejection: true });

  await expect(page.locator(".signin-banner--error")).toBeVisible();
  await expect(page).toHaveURL(/\/clinic\/login/);
  await expect(page.locator("header.shell-topbar")).toHaveCount(0);

  const session = await page.request.get("/api/session");
  expect(session.status()).toBe(401);

  // A rejected login must not open the lab page either.
  await page.goto("/clinic/lab/photos");
  await expect(page).toHaveURL(/\/clinic\/login(\?|$)/);
  await expect(page.locator(".photos-grid")).toHaveCount(0);

  guard.assertNone();
});
