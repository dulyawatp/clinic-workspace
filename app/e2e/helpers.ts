import { readFileSync } from "node:fs";
import { test as base, type Page } from "@playwright/test";
import { getResponse } from "msw";
import { ROUTES } from "../src/app/routes";
import type { RoleCode } from "../src/domain/capability";
import { createHandlers } from "../src/test/msw/handlers";

/** Optional no-backend mode uses the existing fixture handlers, never a socket. */
export const test = base.extend({
  page: async ({ page }, use) => {
    if (process.env.CLINIC_E2E_NO_BACKEND === "1") {
      const handlers = createHandlers({ authed: false });
      await page.route("**/api/**", async (route) => {
        const request = route.request();
        const body = request.postData();
        const response = await getResponse(
          handlers,
          new Request(request.url(), {
            method: request.method(),
            headers: request.headers(),
            ...(body === null ? {} : { body }),
          }),
          { baseUrl: new URL(request.url()).origin },
        );
        if (!response)
          throw new Error(
            `Unhandled fixture request: ${request.method()} ${new URL(request.url()).pathname}`,
          );
        await route.fulfill({
          status: response.status,
          headers: Object.fromEntries(response.headers),
          body: Buffer.from(await response.arrayBuffer()),
        });
      });
    }
    await use(page);
  },
});

interface FixtureAccount {
  usr: string;
  pwd: string;
  user: { id: string; displayName: string; roles: string[] };
}

// Single fixture source: the same account list the proxy authenticates against.
const ACCOUNTS: readonly FixtureAccount[] = JSON.parse(
  readFileSync(
    new URL("../../contracts/fixtures/accounts.json", import.meta.url),
    "utf8",
  ),
);

function accountFor(role: RoleCode): FixtureAccount {
  const account = ACCOUNTS.find((entry) => entry.user.roles.includes(role));
  if (account === undefined) {
    throw new Error(`No fixture account for role ${role}`);
  }
  return account;
}

/**
 * Signs in through the real form; cookies are never injected directly. In
 * no-backend mode the shared fixture handlers simulate the API handshake.
 */
export async function loginAs(page: Page, role: RoleCode): Promise<void> {
  const account = accountFor(role);
  await page.goto(ROUTES.login);
  await page.locator('input[name="usr"]').fill(account.usr);
  await page.locator('input[name="pwd"]').fill(account.pwd);
  await page.locator('button[type="submit"]').click();
  await page.locator("header.shell-topbar").waitFor({ state: "visible" });
}
