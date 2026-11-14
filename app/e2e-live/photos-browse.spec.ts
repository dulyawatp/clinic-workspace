import { expect, type Page, test } from "@playwright/test";
import {
  type LiveCredential,
  MISSING_PRIMARY_REASON,
  primaryCredential,
} from "./credentials";
import {
  EN,
  guardWrites,
  LIVE_CONTENT_SRC,
  LIVE_ORIGIN,
  signIn,
  TH,
  waitForShell,
} from "./helpers";

/**
 * Browse-only acceptance against the deployed synthetic lab. Nothing here
 * creates, saves or mutates: the only non-GET calls are login/logout, enforced
 * by `guardWrites`, and the save control is asserted disabled rather than used.
 */
const credential = primaryCredential();

interface GalleryState {
  tiles: number;
  images: number;
  loaded: number;
  errors: number;
  srcs: string[];
}

interface ContentStatus {
  path: string;
  status: number;
}

async function readGalleryState(page: Page): Promise<GalleryState> {
  return page.evaluate(() => {
    const grid = document.querySelector(".photos-grid");
    const images = grid ? Array.from(grid.querySelectorAll("img")) : [];
    return {
      tiles: grid ? grid.querySelectorAll(".photos-tile").length : 0,
      images: images.length,
      loaded: images.filter(
        (node) => node.naturalWidth > 0 && node.naturalHeight > 0,
      ).length,
      errors: grid ? grid.querySelectorAll(".photos-image-error").length : 0,
      srcs: images.map((node) => node.getAttribute("src") ?? ""),
    };
  });
}

function contentSummary(statuses: readonly ContentStatus[]): string {
  if (statuses.length === 0) return "content responses=[]";
  return `content responses=[${statuses
    .slice(-8)
    .map((entry) => `${entry.path}:${entry.status}`)
    .join(", ")}]`;
}

/**
 * Every rendered tile must keep an image element and decode it. Waiting stops as
 * soon as a tile replaces its image with the error panel, or when all tiles have
 * decoded; the tile count itself is never assumed, only read from the page.
 */
async function expectEveryTileDecoded(
  page: Page,
  statuses: readonly ContentStatus[],
): Promise<string[]> {
  await expect(page.locator(".photos-grid")).toBeVisible();
  const deadline = Date.now() + 30_000;
  let state = await readGalleryState(page);
  while (Date.now() < deadline) {
    if (state.errors > 0) break;
    if (
      state.tiles > 0 &&
      state.images === state.tiles &&
      state.loaded === state.images
    )
      break;
    await page.waitForTimeout(200);
    state = await readGalleryState(page);
  }

  expect(
    state.tiles,
    "the authorized lab patient must expose at least one saved photo",
  ).toBeGreaterThan(0);
  const summary = `tiles=${state.tiles} images=${state.images} decoded=${state.loaded} errorTiles=${state.errors} ${contentSummary(statuses)}`;
  expect(
    state.errors,
    `every saved photo tile must decode; no tile may show the image error panel — ${summary}`,
  ).toBe(0);
  expect(
    state.images,
    `every saved photo tile must render an image element — ${summary}`,
  ).toBe(state.tiles);
  expect(
    state.loaded,
    `every saved photo tile must decode in the browser (naturalWidth/naturalHeight > 0) — ${summary}`,
  ).toBe(state.images);
  return state.srcs;
}

/** One locale pass: gallery heading, every image decoded, disabled save controls. */
async function expectReadOnlyGallery(
  page: Page,
  locale: {
    gallery: string;
    saveDisabled: string;
    saveButton: string;
  },
  statuses: readonly ContentStatus[],
  label: "th" | "en",
): Promise<void> {
  await expect(
    page.getByRole("heading", { name: locale.gallery }),
  ).toBeVisible();

  const srcs = await expectEveryTileDecoded(page, statuses);
  test.info().annotations.push({
    type: "gallery-tiles",
    description: `${label}: ${srcs.length} tiles, ${srcs.length} decoded`,
  });
  for (const src of srcs) {
    expect(src).toMatch(LIVE_CONTENT_SRC);
    // Status and headers of the same URL, fetched with the browser's own
    // session. The response body is never read, logged or attached.
    const content = await page.request.get(src);
    expect(content.status()).toBe(200);
    expect(content.headers()["content-type"] ?? "").toMatch(/^image\//);
    expect(content.headers()["cache-control"] ?? "").toBe("no-store");
  }

  const save = page.locator("section.photos-save");
  await expect(save).toBeVisible();
  await expect(save.getByText(locale.saveDisabled)).toBeVisible();
  await expect(
    save.getByRole("button", { name: locale.saveButton }),
  ).toBeDisabled();
  await expect(save.locator('input[type="file"]')).toBeDisabled();
}

test("physician browses live lab photos read-only in Thai and English", async ({
  page,
}) => {
  test.skip(credential === null, MISSING_PRIMARY_REASON);
  const live = credential as LiveCredential;
  const guard = await guardWrites(page);

  // Diagnostic trail for failures: status codes and paths only, never bodies.
  const statuses: ContentStatus[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === LIVE_ORIGIN && url.pathname.endsWith("/content"))
      statuses.push({ path: url.pathname, status: response.status() });
  });

  await signIn(page, live);
  await waitForShell(page);

  // Live lab entry: the patient list only renders for an authenticated live-mode
  // clinician, so reaching this branch proves live mode rather than fixtures.
  await page.goto("/clinic/lab/photos");
  await expect(page.getByRole("heading", { name: TH.labTitle })).toBeVisible();
  await expect(page.getByRole("heading", { name: TH.choose })).toBeVisible();

  const links = page.locator(".photos-live-list a");
  const patients = await links.count();
  expect(
    patients,
    "the deployed synthetic patient mapping must list at least one patient",
  ).toBeGreaterThan(0);
  await expect(links.first()).toHaveAttribute(
    "href",
    /^\/clinic\/lab\/photos\/[^/]+$/,
  );
  await links.first().click();
  await expect(page).toHaveURL(/\/clinic\/lab\/photos\/[^/]+$/);

  // Thai is the deployed default locale.
  await expectReadOnlyGallery(page, TH, statuses, "th");

  // English locale: same read surface, same disabled save control.
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByRole("heading", { name: EN.labTitle })).toBeVisible();
  await expectReadOnlyGallery(page, EN, statuses, "en");

  // Back to Thai, still read-only.
  await page.getByRole("button", { name: "TH", exact: true }).click();
  await expect(page.getByRole("heading", { name: TH.gallery })).toBeVisible();
  await expect(
    page
      .locator("section.photos-save")
      .getByRole("button", { name: TH.saveButton }),
  ).toBeDisabled();

  // Fixture surfaces fail closed on a live deployment: no fixture rows and no
  // fixture bytes are served. Status codes only; bodies are never read.
  const fixtureRead = await page.request.get(
    "/api/read/clinical-visits?limit=1",
  );
  expect(fixtureRead.status()).toBe(404);
  const fixtureFile = await page.request.get("/api/files/fixture-photo-1");
  expect(fixtureFile.status()).toBe(404);

  guard.assertNone();
});
