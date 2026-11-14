import { readFileSync } from "node:fs";
import { expect, type Page } from "@playwright/test";
import { MARLEY_BASE_URL } from "../playwright.marley.config";
import type { MarleyAccount } from "./marley-env";
import { PAGE } from "./roles";

/** The only origin a run may talk to: the newly launched local app. */
export const MARLEY_ORIGIN = new URL(MARLEY_BASE_URL).origin;

/** Login and logout are the only state changes these tests may cause. */
const AUTH_POST_PATHS = new Set(["/api/auth/login", "/api/auth/logout"]);

/** Thai is the app's default locale; assertions read the shipped catalogue. */
const CATALOG: Record<string, string> = JSON.parse(
  readFileSync(new URL("../src/i18n/catalog/th.json", import.meta.url), "utf8"),
) as Record<string, string>;

export function catalog(key: string): string {
  const value = CATALOG[key];
  if (typeof value !== "string")
    throw new Error(`missing Thai catalogue key: ${key}`);
  return value;
}

export interface WriteGuard {
  /** Method plus path of every blocked request; empty on a clean run. */
  blocked: string[];
  assertNone: () => void;
}

/**
 * Installs the request guard before the first navigation. Reads (GET/HEAD/
 * OPTIONS) and the two auth calls are allowed; every other method and every
 * cross-origin request is aborted locally and fails the test, so no product
 * write can reach the proxy or the site.
 */
export async function guardWrites(page: Page): Promise<WriteGuard> {
  const blocked: string[] = [];
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();
    if (url.origin !== MARLEY_ORIGIN) {
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
        "a non-auth write was attempted (blocked before reaching the app)",
      ).toEqual([]),
  };
}

/**
 * Signs in through the real form. The password is typed into the page and is
 * never used in a title, assertion message or console line.
 */
export async function signIn(
  page: Page,
  account: MarleyAccount,
): Promise<void> {
  await page.goto(PAGE.login);
  await page.locator('input[name="usr"]').fill(account.user);
  await page.locator('input[name="pwd"]').fill(account.password);
  await page.locator('button[type="submit"]').click();

  const banner = page.locator(".signin-banner--error");
  const shell = page.locator("header.shell-topbar");
  await Promise.race([
    shell.waitFor({ state: "visible", timeout: 40_000 }).catch(() => {}),
    banner.waitFor({ state: "visible", timeout: 40_000 }).catch(() => {}),
  ]);
  if ((await banner.count()) > 0 && (await banner.isVisible())) {
    const reason =
      (await banner.textContent())?.replace(/\s+/g, " ").trim() ?? "";
    if (reason.includes(catalog("errors.RATE_LIMITED")))
      throw new Error(
        "RATE_LIMITED: login throttle refused this handshake; the role case is UNTESTED, never a product failure. Wait for the 10-minute window and re-run.",
      );
    throw new Error(`sign-in was rejected: ${reason}`);
  }
  await expect(shell).toBeVisible();
}

export interface SessionBody {
  mode?: string;
  sessionEpoch?: string;
  user?: { id?: string; displayName?: string; roles?: string[] };
  capabilities?: Record<string, string>;
}

/** `GET /api/session` from inside the authenticated page (read-only). */
export async function readSession(
  page: Page,
): Promise<{ status: number; body: SessionBody | null }> {
  const result = await page.evaluate(async (path) => {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    return { status: response.status, text: await response.text() };
  }, "/api/session");
  let body: SessionBody | null = null;
  try {
    body = JSON.parse(result.text) as SessionBody;
  } catch {
    body = null;
  }
  return { status: result.status, body };
}

export interface ApiProbe {
  status: number;
  contentType: string;
  cacheControl: string;
  bytes: number;
}

/** A same-origin GET issued by the page, used for allow/deny assertions. */
export async function apiGet(page: Page, path: string): Promise<ApiProbe> {
  return page.evaluate(async (target) => {
    const response = await fetch(target, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    const buffer = await response.arrayBuffer();
    return {
      status: response.status,
      contentType: response.headers.get("content-type") ?? "",
      cacheControl: response.headers.get("cache-control") ?? "",
      bytes: buffer.byteLength,
    };
  }, path);
}

/** Absolute hrefs of the sidebar, as paths, for nav allow-list assertions. */
export async function sidebarPaths(page: Page): Promise<string[]> {
  const hrefs = await page
    .locator("aside.shell-sidebar a.shell-nav-link")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("href") ?? ""),
    );
  return hrefs
    .map((href) => {
      try {
        return new URL(href, MARLEY_ORIGIN).pathname;
      } catch {
        return href;
      }
    })
    .sort();
}

/** Signs out through the real profile control and waits for the login page. */
export async function signOut(page: Page): Promise<void> {
  await page.goto(PAGE.profile);
  await page.getByRole("button", { name: catalog("action.signOut") }).click();
  await page.waitForURL((url) => url.pathname === PAGE.login, {
    timeout: 40_000,
  });
}

/** Numeric text as the app formats it for the Thai locale. */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat("th", { maximumFractionDigits: 2 }).format(
    value,
  );
}
