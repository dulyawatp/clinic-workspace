import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { MARLEY_BASE_URL } from "../playwright.marley.config";
import {
  apiGet,
  catalog,
  guardWrites,
  readSession,
  sidebarPaths,
  signIn,
  signOut,
  type WriteGuard,
} from "./harness";
import { loadRuntime } from "./marley-env";
import { API, deniedLiveReads, livePhotos, PAGE, roleCases } from "./roles";

/**
 * Marley real-UI seven-role matrix.
 *
 * Every case is a real browser against the real local proxy and the real
 * `marley.local` site through the SSH tunnel. Reads only: the sole non-GET
 * requests are login and logout, guarded at the browser boundary.
 */

const runtime = loadRuntime();
const CASES = roleCases(runtime);
const REFERENCE = runtime.live.patientReference;

/** The exact appointment DTO keys the proxy may emit, sorted. */
const APPOINTMENT_KEYS =
  "appointment_date,appointment_id,appointment_time,status,workspace_patient_id";

/**
 * A same-origin GET with its parsed JSON body. Only schema-level facts are
 * asserted from `body`; a value is never printed, logged or embedded in a
 * title, so an unexpected identifier cannot leak through a failure message.
 */
async function apiJson(
  page: Page,
  path: string,
): Promise<{ status: number; cacheControl: string; body: unknown }> {
  return page.evaluate(async (target) => {
    const response = await fetch(target, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
    return {
      status: response.status,
      cacheControl: response.headers.get("cache-control") ?? "",
      body,
    };
  }, path);
}

/**
 * Mirrors the proxy/client appointment invariants as booleans, so a failed
 * assertion reports `false` rather than the offending synthetic value.
 */
function safeIdentifier(value: unknown): boolean {
  if (typeof value !== "string") return false;
  if (value.trim() !== value || value.length === 0 || value.length > 256)
    return false;
  return ![...value].some((char) => {
    const code = char.charCodeAt(0);
    return code < 32 || code === 127;
  });
}

function calendarDate(value: unknown): boolean {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const [year, month, day] = value.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined)
    return false;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function appointmentRows(body: unknown): unknown[] | null {
  if (typeof body !== "object" || body === null) return null;
  const items = (body as Record<string, unknown>).items;
  return Array.isArray(items) && items.length <= 1_000 ? items : null;
}

function isAppointmentRow(row: unknown): boolean {
  if (typeof row !== "object" || row === null || Array.isArray(row))
    return false;
  const value = row as Record<string, unknown>;
  return (
    safeIdentifier(value.workspace_patient_id) &&
    safeIdentifier(value.appointment_id) &&
    calendarDate(value.appointment_date) &&
    typeof value.appointment_time === "string" &&
    /^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(value.appointment_time) &&
    typeof value.status === "string" &&
    /^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(value.status)
  );
}

/** Sorted key list of one row; schema names only, never a value. */
function rowKeys(row: unknown): string {
  if (typeof row !== "object" || row === null || Array.isArray(row)) return "";
  return Object.keys(row).sort().join(",");
}

/** The machine code of a refusal body, or `""` when the body is not shaped. */
function errorCode(body: unknown): string {
  if (typeof body !== "object" || body === null) return "";
  const code = (body as Record<string, unknown>).code;
  return typeof code === "string" ? code : "";
}

/** Opens one isolated context per role; no storage, cookie or page is shared. */
async function openRoleContext(
  context: BrowserContext,
): Promise<{ page: Page; guard: WriteGuard }> {
  const page = await context.newPage();
  const guard = await guardWrites(page);
  return { page, guard };
}

/**
 * Reads the one saved synthetic photo the way an authorized clinician does:
 * the gallery tile, the private bytes and the decoded image.
 */
async function expectSavedPhoto(page: Page): Promise<void> {
  const contentResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/content") &&
      response.request().resourceType() === "image",
    { timeout: 60_000 },
  );
  await page.goto(PAGE.livePhoto(REFERENCE));
  // The legacy detail address forwards to the designed PAT-04 workspace.
  await page.waitForURL(
    (url) => url.pathname === PAGE.livePhotoWorkspace(REFERENCE),
    { timeout: 40_000 },
  );
  const response = await contentResponse;

  expect(response.status(), "saved photo bytes must be served").toBe(200);
  expect(response.headers()["content-type"] ?? "").toMatch(/^image\//);
  expect(response.headers()["cache-control"] ?? "").toContain("no-store");
  expect((await response.body()).byteLength).toBeGreaterThan(0);

  const tiles = page.locator("figure.photos-tile");
  await expect(tiles.first()).toBeVisible();
  expect(await tiles.count()).toBeGreaterThan(0);
  const image = tiles.first().locator("img");
  await expect(image).toHaveAttribute(
    "src",
    /^\/api\/live\/synthetic-patients\/[^/]+\/photos\/[^/]+\/content$/,
  );
  const natural = await image.evaluate((node) => ({
    width: node.naturalWidth,
    height: node.naturalHeight,
  }));
  expect(natural.width, "browser decoded the saved image").toBeGreaterThan(0);
  expect(natural.height, "browser decoded the saved image").toBeGreaterThan(0);

  // The workspace belongs to PAT-04: its back link is the designed gallery.
  await expect(page.locator(".photos-heading a")).toHaveAttribute(
    "href",
    PAGE.livePhotos(REFERENCE),
  );
}

/**
 * The legacy lab-photo index no longer lists patients: it is a role-guarded
 * redirect, so a clinician lands on the designed home. The picker it used to
 * render has no routed address any more.
 */
async function expectLabIndexRedirect(page: Page): Promise<void> {
  await page.goto(PAGE.livePhotosIndex);
  await page.waitForURL((url) => url.pathname === PAGE.home, {
    timeout: 40_000,
  });
  await expect(page.getByTestId("role-home")).toBeVisible();
}

async function expectDirectoryRow(page: Page): Promise<void> {
  await page.goto(PAGE.patients);
  const rows = page.locator("tr.patients-row");
  await expect(rows.first()).toBeVisible();
  await expect(rows.first()).toContainText(REFERENCE);
  const name = (
    await rows.first().locator("td.patients-name").textContent()
  ).trim();
  expect(name.length).toBeGreaterThan(0);
}

async function expectPharmacyItem(page: Page): Promise<void> {
  await page.goto(PAGE.pharmacy);
  const rows = page.locator("table.pharmacy-table tbody tr");
  await expect(rows).toHaveCount(1);
  // The allow-listed name is exact; the private probe keeps item_name as a
  // shape only, so its rendered text is asserted non-empty, not compared.
  await expect(rows.first().locator("td").nth(1)).toHaveText(
    runtime.live.itemCode,
  );
  const name = (await rows.first().locator("td strong").textContent()).trim();
  expect(name.length).toBeGreaterThan(0);
  const unit = (await rows.first().locator("td").nth(2).textContent()).trim();
  expect(unit.length).toBeGreaterThan(0);
}

/**
 * The React-era `/clinic/finance` aggregate was folded into the designed
 * surfaces, so the old deep link must resolve to the role's finance owner:
 * CAS-01 for billing roles, MGR-01 for the owner/manager, and the designed
 * home for every other role.
 */
async function expectFinanceDestination(
  page: Page,
  code: string,
): Promise<void> {
  const management = code === "MGR";
  const target = management ? PAGE.management : PAGE.cashier;
  const surface = management ? "MGR-01" : "CAS-01";
  const testId = management ? "management-page" : "cashier-page";
  await page.goto(PAGE.legacyFinance);
  await page.waitForURL((url) => url.pathname === target, {
    timeout: 40_000,
  });
  await expect(page.getByTestId(testId)).toHaveAttribute(
    "data-surface",
    surface,
  );
}

async function expectAuditSurface(page: Page): Promise<void> {
  await page.goto(PAGE.system);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".system-intro")).toContainText(
    catalog("system.demoNote"),
  );
  await expect(page.locator(".patients-row, .pharmacy-table")).toHaveCount(0);
}

for (const item of CASES) {
  const code = item.role;
  const label = catalog(`role.${code.toLowerCase()}.short`);

  test.describe(`${code} · ${label}`, () => {
    test.describe.configure({ mode: "serial" });

    let context: BrowserContext;
    let page: Page;
    let guard: WriteGuard;

    test.beforeAll(async ({ browser }) => {
      context = await browser.newContext({
        baseURL: MARLEY_BASE_URL,
        acceptDownloads: false,
        serviceWorkers: "block",
        ignoreHTTPSErrors: false,
      });
      ({ page, guard } = await openRoleContext(context));
      await signIn(page, item.account);
    });

    test.afterAll(async () => {
      await context?.close();
    });

    test(`MAR-AUTH-${code} form login, live session and role`, async () => {
      // Real form login already happened in beforeAll; the shell proves it.
      await expect(page.locator("header.shell-topbar")).toBeVisible();
      await expect(page.locator(".shell-role strong")).toHaveText(label);

      const session = await readSession(page);
      expect(session.status).toBe(200);
      expect(session.body?.mode).toBe("live");
      expect(session.body?.user?.roles).toEqual([code]);
      expect(session.body?.user?.id).toBe(item.account.user);
      expect(
        (session.body?.user?.displayName ?? "").trim().length,
      ).toBeGreaterThan(0);
      expect((session.body?.sessionEpoch ?? "").trim().length).toBeGreaterThan(
        0,
      );

      // Publication is disabled: every write capability is read_only.
      const writes = Object.entries(session.body?.capabilities ?? {}).filter(
        ([name]) => name.startsWith("write."),
      );
      expect(writes.length).toBeGreaterThan(0);
      for (const [name, state] of writes)
        expect(`${name}=${state}`).toBe(`${name}=read_only`);
    });

    test(`MAR-NAV-${code} sidebar destination allow-list`, async () => {
      expect(await sidebarPaths(page)).toEqual([...item.nav].sort());
      guard.assertNone();
    });

    test(`MAR-DATA-${code} ${item.dataCase}`, async () => {
      if (code === "DOC") {
        await expectLabIndexRedirect(page);
        await expectSavedPhoto(page);

        const save = page.locator("section.photos-save");
        await expect(save).toBeVisible();
        await expect(save.locator('input[type="file"]')).toBeDisabled();
        await expect(
          save.getByRole("button", { name: catalog("photoSave.save") }),
        ).toBeDisabled();
        await expect(save).toContainText(catalog("photoSave.unavailable"));
      } else if (code === "NUR") {
        await expectLabIndexRedirect(page);
        await expectSavedPhoto(page);
        // The save region is not rendered for a read-only bedside role.
        await expect(page.locator("section.photos-save")).toHaveCount(0);
      } else if (code === "REC") {
        await expectDirectoryRow(page);
      } else if (code === "PHA") {
        await expectPharmacyItem(page);
      } else if (code === "CAS" || code === "MGR") {
        await expectFinanceDestination(page, code);
        if (code === "MGR") await expectAuditSurface(page);
      } else {
        await expectAuditSurface(page);
      }
      guard.assertNone();
    });

    test(`MAR-DENY-${code} cross-role API and live-mode fail-closed`, async () => {
      for (const path of deniedLiveReads(item, CASES)) {
        const probe = await apiGet(page, path);
        expect(`${path} ${probe.status}`).toBe(`${path} 403`);
      }

      // A role may always read its own surface (positive control).
      for (const path of item.liveReads) {
        const probe = await apiGet(page, path);
        expect(`${path} ${probe.status}`).toBe(`${path} 200`);
        expect(probe.cacheControl).toContain("no-store");
      }

      // Fixture rows and fixture private bytes never answer in live mode.
      expect((await apiGet(page, API.fixturePatients)).status).toBe(404);
      expect((await apiGet(page, API.fixtureFile)).status).toBe(404);

      // No non-auth write was ever attempted by the UI.
      guard.assertNone();
    });

    test(`MAR-APPT-${code} appointment endpoint role boundary`, async () => {
      const probe = await apiJson(page, API.liveAppointments);
      expect(probe.cacheControl, "appointments must never be cached").toContain(
        "no-store",
      );

      if (item.liveReads.includes(API.liveAppointments)) {
        // Positive control: the shape is asserted, never a populated list —
        // the suite's proxy is not configured with synthetic appointments.
        expect(`${code} ${probe.status}`).toBe(`${code} 200`);
        const rows = appointmentRows(probe.body);
        expect(rows !== null, "appointment body must be { items: [...] }").toBe(
          true,
        );
        for (const row of rows ?? []) {
          expect(
            rowKeys(row),
            "appointment row must expose the fixed DTO",
          ).toBe(APPOINTMENT_KEYS);
          expect(
            isAppointmentRow(row),
            "appointment row must satisfy the documented DTO invariants",
          ).toBe(true);
        }
        const ids = (rows ?? []).map(
          (row) => (row as Record<string, unknown>).appointment_id,
        );
        expect(
          new Set(ids).size,
          "appointment ids must be unique within one response",
        ).toBe(ids.length);
      } else {
        // A direct request is refused even though the page is unreachable from
        // this role's sidebar: navigation and authorization are asserted apart.
        expect(`${code} ${probe.status}`).toBe(`${code} 403`);
        expect(errorCode(probe.body)).toBe("FORBIDDEN");
      }

      guard.assertNone();
    });

    test(`MAR-RECOVERY-${code} draft recovery stays disabled`, async () => {
      const probe = await apiJson(page, API.liveDraftRecovery);
      // The run sets no `CLINIC_LIVE_ENCOUNTER_WRITES`, so this gate closes
      // before role and credential checks. It proves the gate is closed — never
      // that recovery works, and no write capability was enabled to look.
      expect(`${code} ${probe.status}`).toBe(`${code} 403`);
      expect(
        errorCode(probe.body),
        "draft recovery must answer the write-disabled code, not a role refusal",
      ).toBe("WRITE_DISABLED");
      expect(probe.cacheControl).toContain("no-store");
      guard.assertNone();
    });

    test(`MAR-LOGOUT-${code} sign-out and post-logout denial`, async () => {
      await signOut(page);
      expect(new URL(page.url()).pathname).toBe(PAGE.login);

      expect((await readSession(page)).status).toBe(401);

      await page.goto(PAGE.patients);
      await page.waitForURL((url) => url.pathname === PAGE.login, {
        timeout: 40_000,
      });
      guard.assertNone();
    });
  });
}

test.describe("MAR-MATRIX invariants", () => {
  test("MAR-SPEC-001 seven distinct roles with distinct accounts", () => {
    expect(CASES).toHaveLength(7);
    expect(new Set(CASES.map((item) => item.role)).size).toBe(7);
    expect(new Set(CASES.map((item) => item.account.user)).size).toBe(7);
  });

  test("MAR-SPEC-002 live photo path is allow-listed for readers only", () => {
    const readers = CASES.filter((item) =>
      item.liveReads.includes(livePhotos(REFERENCE)),
    ).map((item) => item.role);
    expect(readers.sort()).toEqual(["DOC", "NUR"]);
  });

  test("MAR-SPEC-003 appointment reads are allow-listed for DOC and REC only", () => {
    const readers = CASES.filter((item) =>
      item.liveReads.includes(API.liveAppointments),
    ).map((item) => item.role);
    expect(readers.sort()).toEqual(["DOC", "REC"]);
  });
});
