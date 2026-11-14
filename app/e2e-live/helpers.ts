import { expect, type Page } from "@playwright/test";
import { LIVE_BASE_URL } from "../playwright.live.config";
import type { LiveCredential } from "./credentials";

/** The only origin a live run may talk to: the existing loopback tunnel. */
export const LIVE_ORIGIN = new URL(LIVE_BASE_URL).origin;

/**
 * The only non-GET requests a browse-only run may issue. `photo-save` and
 * `photo-status` are deliberately absent, so any attempt is blocked locally and
 * fails the test instead of reaching the deployed site.
 */
const AUTH_POST_PATHS = new Set(["/api/auth/login", "/api/auth/logout"]);

export interface WriteGuard {
  /** Method plus path of every blocked request; empty on a clean run. */
  blocked: string[];
  assertNone: () => void;
}

/**
 * Installs a request guard before the first navigation. GET/HEAD/OPTIONS are
 * allowed (reads, CSRF handshake, image content); only login/logout may POST.
 * Cross-origin requests are blocked too, so a stray tunnel or CDN call cannot
 * carry the session cookie off-origin.
 */
export async function guardWrites(page: Page): Promise<WriteGuard> {
  const blocked: string[] = [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();
    if (url.origin !== LIVE_ORIGIN) {
      blocked.push(`${method} ${url.origin}${url.pathname}`);
      return route.abort("blockedbyclient");
    }
    if (method === "GET" || method === "HEAD" || method === "OPTIONS")
      return route.continue();
    if (method === "POST" && AUTH_POST_PATHS.has(url.pathname))
      return route.continue();
    blocked.push(`${method} ${url.pathname}`);
    return route.abort("blockedbyclient");
  });
  return {
    blocked,
    assertNone: () =>
      expect(
        blocked,
        "browse-only run attempted a non-auth write (blocked before reaching the site)",
      ).toEqual([]),
  };
}

/**
 * Signs in through the real form. Credentials are typed into the page and never
 * placed in a title, assertion message or console line; the password field is
 * never asserted against.
 */
export async function signIn(
  page: Page,
  credential: LiveCredential,
  options: { expectRejection?: boolean } = {},
): Promise<void> {
  await page.goto("/clinic/login");
  await page.locator('input[name="usr"]').fill(credential.user);
  await page.locator('input[name="pwd"]').fill(credential.password);
  await page.locator('button[type="submit"]').click();

  // Fail with the rendered reason (rate limit vs. rejected credentials) instead
  // of an opaque shell timeout. The banner copy contains no secret.
  const banner = page.locator(".signin-banner--error");
  const shell = page.locator("header.shell-topbar");
  await Promise.race([
    shell.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {}),
    banner.waitFor({ state: "visible", timeout: 30_000 }).catch(() => {}),
  ]);
  if (
    options.expectRejection !== true &&
    (await banner.count()) > 0 &&
    (await banner.isVisible())
  ) {
    const reason =
      (await banner.textContent())?.replace(/\s+/g, " ").trim() ?? "";
    throw new Error(`sign-in was rejected: ${reason}`);
  }
}

/** Waits for the authenticated shell, proving the login handshake completed. */
export async function waitForShell(page: Page): Promise<void> {
  await page.locator("header.shell-topbar").waitFor({
    state: "visible",
    timeout: 30_000,
  });
}

export const TH = {
  loginTitle: "เข้าสู่ระบบ",
  labTitle: "ภาพข้อมูลจำลองในห้องทดลอง",
  choose: "เลือกผู้ป่วยห้องทดลอง",
  gallery: "ภาพห้องทดลองที่บันทึกแล้ว",
  saveDisabled: "ไม่สามารถบันทึกได้หากไม่มีสิทธิ์และข้อมูลเชื่อมโยงผู้ป่วยห้องทดลองที่ตรวจสอบแล้ว",
  saveButton: "บันทึกภาพทดสอบ",
} as const;

export const EN = {
  labTitle: "Synthetic lab photos",
  gallery: "Saved lab photos",
  saveDisabled:
    "Saving is unavailable without permission and a verified lab patient mapping.",
  saveButton: "Save test image",
} as const;

/** `/api/live/synthetic-patients/<ref>/photos/<id>/content` and nothing else. */
export const LIVE_CONTENT_SRC =
  /^\/api\/live\/synthetic-patients\/[A-Za-z0-9][A-Za-z0-9._:-]{0,139}\/photos\/[A-Za-z0-9][A-Za-z0-9._-]{0,139}\/content$/;
