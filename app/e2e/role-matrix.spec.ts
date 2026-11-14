import { expect, type Page } from "@playwright/test";
import { ROUTES } from "../src/app/routes";
import type { RoleCode } from "../src/domain/capability";
import { loginAs, test } from "./helpers";

/**
 * FIXTURE ROLE MATRIX — synthetic data only.
 *
 * Every account below is declared in `contracts/fixtures/accounts.json` and is
 * created by the fixture proxy or the shared MSW handlers. These are NOT native
 * Lab identities and this suite never touches production or a live lab: no
 * write is ever submitted, every write control is asserted disabled. Run with
 * `CLINIC_E2E_NO_BACKEND=1` so every API call is served by the shared fixture
 * handlers (no proxy socket, no lab).
 *
 * Nav expectations are written out by hand (not imported from the nav manifest)
 * so a drift in `nav.ts` or `access.json` fails here instead of agreeing with
 * itself. Visibility is read from the same interim read matrix both the proxy
 * and MSW enforce.
 */

const ALL_NAV = [
  ROUTES.home,
  ROUTES.search,
  ROUTES.patients,
  ROUTES.reception,
  ROUTES.queue,
  ROUTES.checkout,
  ROUTES.appointments,
  ROUTES.profile,
  ROUTES.system,
] as const;

type Matrix = {
  /** Text the shell shows for the single signed-in fixture role. */
  label: RegExp;
  /** Exactly the destinations the sidebar may advertise, in manifest order. */
  nav: readonly string[];
  /** A permitted surface the role should be able to open from the nav. */
  open: string;
};

const MATRIX: Record<RoleCode, Matrix> = {
  REC: {
    label: /Receptionist|เจ้าหน้าที่ต้อนรับ/,
    nav: [
      ROUTES.home,
      ROUTES.search,
      ROUTES.patients,
      ROUTES.reception,
      ROUTES.queue,
      ROUTES.checkout,
      ROUTES.appointments,
      ROUTES.profile,
    ],
    open: ROUTES.reception,
  },
  DOC: {
    label: /Doctor|แพทย์/,
    nav: [
      ROUTES.home,
      ROUTES.search,
      ROUTES.patients,
      ROUTES.appointments,
      ROUTES.profile,
    ],
    open: ROUTES.patients,
  },
  NUR: {
    label: /Nurse|พยาบาล/,
    nav: [
      ROUTES.home,
      ROUTES.search,
      ROUTES.patients,
      ROUTES.queue,
      ROUTES.profile,
    ],
    open: ROUTES.queue,
  },
  PHA: {
    label: /Pharmacy|เภสัช/,
    nav: [ROUTES.home, ROUTES.search, ROUTES.profile],
    open: ROUTES.profile,
  },
  CAS: {
    label: /Cashier|การเงิน/,
    nav: [ROUTES.home, ROUTES.search, ROUTES.checkout, ROUTES.profile],
    open: ROUTES.checkout,
  },
  MGR: {
    label: /manager|ผู้จัดการ/i,
    nav: [ROUTES.home, ROUTES.search, ROUTES.profile, ROUTES.system],
    open: ROUTES.system,
  },
  ADM: {
    label: /Administrator|ผู้ดูแลระบบ/,
    nav: [
      ROUTES.home,
      ROUTES.search,
      ROUTES.patients,
      ROUTES.profile,
      ROUTES.system,
    ],
    // ADM no longer sees appointments; the system page is its admin surface.
    open: ROUTES.system,
  },
};

const ROLES = Object.keys(MATRIX) as RoleCode[];

const navLink = (page: Page, path: string) =>
  page.locator(`aside.shell-sidebar a.shell-nav-link[href="${path}"]`);

/** `toHaveURL` matches the whole absolute URL, so anchors stay unanchored. */
const endsWith = (path: string) =>
  new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);

const sidebarLinks = (page: Page) =>
  page.locator("aside.shell-sidebar a.shell-nav-link");

async function navPaths(page: Page): Promise<string[]> {
  const hrefs = await sidebarLinks(page).evaluateAll((links) =>
    links.map((link) => link.getAttribute("href") ?? ""),
  );
  return hrefs.sort();
}

test.describe("fixture role matrix (synthetic accounts, not native sign-in)", () => {
  for (const role of ROLES) {
    const matrix = MATRIX[role];

    test(`fixture ${role}: login, permitted nav only, permitted surface opens`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await loginAs(page, role);

      // Login really bound the fixture role to this session.
      await expect(page.locator(".shell-role strong")).toHaveText(matrix.label);
      await expect(page).toHaveURL(endsWith(ROUTES.home));

      // Positive: every permitted destination is advertised, with its href.
      for (const path of matrix.nav) {
        await expect(navLink(page, path)).toHaveCount(1);
      }
      // Negative: nothing outside the permitted set leaks into the sidebar.
      for (const path of ALL_NAV) {
        if (!matrix.nav.includes(path)) {
          await expect(navLink(page, path)).toHaveCount(0);
        }
      }
      expect(await navPaths(page)).toEqual([...matrix.nav].sort());

      // Positive navigation: the permitted surface opens and marks itself
      // current. No data assertion here: reads stay fixture-only either way.
      await navLink(page, matrix.open).click();
      const current = `aside.shell-sidebar a.shell-nav-link[aria-current="page"]`;
      await expect(page.locator(current)).toHaveCount(1);
      await expect(
        page.locator(`${current}[href="${matrix.open}"]`),
      ).toHaveCount(1);
      expect(new URL(page.url()).pathname).toBe(matrix.open);
      await expect(page.locator("main#main")).toBeVisible();
    });
  }

  test("fixture REC: read-only capability state disables the queue write gate", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAs(page, "REC");
    await navLink(page, ROUTES.queue).click();
    await expect(page.getByRole("table")).toBeVisible();

    const gate = page.getByRole("button", {
      name: /รับผู้ป่วยไม่มีนัด|walk-in check-in/i,
    });
    await expect(gate).toBeVisible();
    await expect(gate).toBeDisabled();
    await expect(gate).toHaveAttribute("aria-disabled", "true");

    // The gate explains the read-only state through aria-describedby; that
    // reason node is the actual user-visible contract, not the button label.
    const describedBy = await gate.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const reasonId = describedBy?.trim().split(/\s+/).at(-1);
    expect(reasonId).toBeTruthy();
    const reason = page.locator(`[id="${reasonId}"]`);
    await expect(reason).toBeVisible();
    await expect(reason).toContainText(
      /ไม่พร้อมใช้งานในระบบนี้|not available in this deployment/i,
    );

    // A disabled gate must not navigate or submit: the URL is unchanged.
    await gate.click({ force: true });
    expect(new URL(page.url()).pathname).toBe(ROUTES.queue);
  });

  test("fixture guest: protected route redirects to login with reason", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(ROUTES.patients);
    await expect(page).toHaveURL(
      new RegExp(`${ROUTES.login}\\?reason=expired$`),
    );
    await expect(page.locator('input[name="usr"]')).toBeVisible();
    await expect(page.locator("header.shell-topbar")).toHaveCount(0);
    await expect(page.locator("aside.shell-sidebar")).toHaveCount(0);
  });

  test("fixture login: unknown credentials keep the guest on the form", async ({
    page,
  }) => {
    await page.goto(ROUTES.login);
    await page.locator('input[name="usr"]').fill("fixture-unknown-role");
    await page.locator('input[name="pwd"]').fill("wrong-fixture-password");
    await page.locator('button[type="submit"]').click();
    await expect(page.getByRole("alert")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe(ROUTES.login);
    await expect(page.locator("header.shell-topbar")).toHaveCount(0);
  });
});
