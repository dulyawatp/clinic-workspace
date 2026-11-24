import { expect, type Page } from "@playwright/test";
import { ROUTES } from "../src/app/routes";
import type { RoleCode } from "../src/domain/capability";
import { LOCALE_KEY } from "../src/i18n/locale-storage";
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
  ROUTES.doctor,
  ROUTES.nurse,
  ROUTES.nurseTasks,
  ROUTES.pharmacy,
  ROUTES.pharmacyItems,
  ROUTES.pharmacyBatches,
  ROUTES.pharmacyReceive,
  ROUTES.pharmacyBalances,
  ROUTES.pharmacyMovements,
  ROUTES.pharmacyPurchasing,
  ROUTES.cashier,
  ROUTES.invoices,
  ROUTES.payments,
  ROUTES.management,
  ROUTES.managementReports,
  ROUTES.ownerStaff,
  ROUTES.admin,
  ROUTES.adminUsers,
  ROUTES.adminClinical,
  ROUTES.adminSettings,
  ROUTES.adminSystem,
] as const;

type Matrix = {
  /** Text the shell shows for the single signed-in fixture role. */
  label: RegExp;
  /** Exactly the destinations the sidebar may advertise, in manifest order. */
  nav: readonly string[];
  /** A permitted surface the role should be able to open from the nav. */
  open: string;
  heading: { th: string; en: string };
};

const MATRIX: Record<RoleCode, Matrix> = {
  REC: {
    label: /Receptionist|เจ้าหน้าที่ต้อนรับ/,
    nav: [
      ROUTES.home,
      ROUTES.reception,
      ROUTES.queue,
      ROUTES.appointments,
      ROUTES.patients,
      ROUTES.checkout,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.reception,
    heading: { th: "หน้าหลักงานต้อนรับ", en: "Reception home" },
  },
  DOC: {
    label: /Doctor|แพทย์/,
    nav: [
      ROUTES.home,
      ROUTES.doctor,
      ROUTES.appointments,
      ROUTES.patients,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.doctor,
    heading: { th: "ตารางวันนี้ของแพทย์", en: "Doctor’s day" },
  },
  NUR: {
    label: /Nurse|พยาบาล/,
    nav: [
      ROUTES.home,
      ROUTES.nurse,
      ROUTES.nurseTasks,
      ROUTES.queue,
      ROUTES.patients,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.nurse,
    heading: { th: "ผู้ป่วยที่ดูแล", en: "Assigned patients" },
  },
  PHA: {
    label: /Pharmacy|เภสัช/,
    nav: [
      ROUTES.home,
      ROUTES.pharmacy,
      ROUTES.pharmacyItems,
      ROUTES.pharmacyBatches,
      ROUTES.pharmacyReceive,
      ROUTES.pharmacyBalances,
      ROUTES.pharmacyMovements,
      ROUTES.pharmacyPurchasing,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.pharmacy,
    heading: { th: "ห้องยา", en: "Pharmacy" },
  },
  CAS: {
    label: /Cashier|การเงิน/,
    nav: [
      ROUTES.home,
      ROUTES.cashier,
      ROUTES.invoices,
      ROUTES.payments,
      ROUTES.checkout,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.cashier,
    heading: { th: "รายการรอเรียกเก็บ", en: "Pending bills" },
  },
  MGR: {
    label: /manager|ผู้จัดการ/i,
    nav: [
      ROUTES.home,
      ROUTES.management,
      ROUTES.managementReports,
      ROUTES.invoices,
      ROUTES.payments,
      ROUTES.pharmacyBatches,
      ROUTES.pharmacyBalances,
      ROUTES.system,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.management,
    heading: { th: "ภาพรวมคลินิก", en: "Clinic overview" },
  },
  ADM: {
    label: /Administrator|ผู้ดูแลระบบ/,
    nav: [
      ROUTES.home,
      ROUTES.admin,
      ROUTES.adminUsers,
      ROUTES.adminClinical,
      ROUTES.adminSettings,
      ROUTES.adminSystem,
      ROUTES.system,
      ROUTES.patients,
      ROUTES.search,
      ROUTES.profile,
    ],
    open: ROUTES.admin,
    heading: { th: "ตั้งค่าระบบ", en: "Setup overview" },
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
  return hrefs;
}

test.describe("fixture role matrix (synthetic accounts, not native sign-in)", () => {
  for (const locale of ["th", "en"] as const)
    for (const role of ROLES) {
      const matrix = MATRIX[role];

      test(`fixture ${role} ${locale}: login, permitted nav only, permitted surface opens`, async ({
        page,
      }) => {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.addInitScript(
          ({ key, locale }) => localStorage.setItem(key, locale),
          { key: LOCALE_KEY, locale },
        );
        await loginAs(page, role);

        // Login really bound the fixture role to this session.
        await expect(page.locator(".shell-role strong")).toHaveText(
          matrix.label,
        );
        await expect(page).toHaveURL(endsWith(ROUTES.home));

        const more = page.locator("aside.shell-sidebar details");
        const primary = page.locator(
          "aside.shell-sidebar .shell-nav-primary > li > a",
        );
        await expect(primary).toHaveCount(Math.min(6, matrix.nav.length));
        expect(
          await primary.evaluateAll((links) =>
            links.map((link) => link.getAttribute("href")),
          ),
        ).toEqual(matrix.nav.slice(0, 6));
        if (matrix.nav.length > 6) {
          await expect(more).not.toHaveAttribute("open", "");
          for (const path of matrix.nav.slice(6))
            await expect(navLink(page, path)).toBeHidden();
          await more.locator("summary").click();
          await expect(more).toHaveAttribute("open", "");
          for (const path of matrix.nav.slice(6))
            await expect(navLink(page, path)).toBeVisible();
        } else {
          await expect(more).toHaveCount(0);
        }
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
        expect(await navPaths(page)).toEqual(matrix.nav);

        // Positive navigation: the permitted surface opens and marks itself
        // current. No data assertion here: reads stay fixture-only either way.
        await navLink(page, matrix.open).click();
        const current = `aside.shell-sidebar a.shell-nav-link[aria-current="page"]`;
        await expect(page.locator(current)).toHaveCount(1);
        await expect(
          page.locator(`${current}[href="${matrix.open}"]`),
        ).toHaveCount(1);
        expect(new URL(page.url()).pathname).toBe(matrix.open);
        await expect(
          page.locator("main#main").getByRole("heading", {
            name: matrix.heading[locale],
            exact: true,
            level: 1,
          }),
        ).toBeVisible();
        await expect(
          page.locator("main#main").getByRole("heading", {
            name: locale === "th" ? "ไม่มีสิทธิ์เข้าถึงหน้านี้" : "Permission denied",
            exact: true,
          }),
        ).toHaveCount(0);
      });
    }

  test("fixture PHA: keyboard opens More and navigates to movements", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAs(page, "PHA");
    const more = page.locator("aside.shell-sidebar details");
    const summary = more.locator("summary");
    await expect(navLink(page, ROUTES.pharmacyMovements)).toBeHidden();
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(more).toHaveAttribute("open", "");
    await page.keyboard.press("Tab");
    await expect(navLink(page, ROUTES.pharmacyMovements)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(endsWith(ROUTES.pharmacyMovements));
    await expect(navLink(page, ROUTES.pharmacyMovements)).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("fixture PHA: mobile More exposes keyboard navigation at 390", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAs(page, "PHA");
    const nav = page.locator("nav.shell-nav--bottom");
    const details = nav.locator("details");
    const destination = nav.locator(`a[href="${ROUTES.pharmacyMovements}"]`);
    await expect(destination).toBeHidden();
    await details.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(destination).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(destination).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(endsWith(ROUTES.pharmacyMovements));
    await expect(destination).toHaveAttribute("aria-current", "page");
  });

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
