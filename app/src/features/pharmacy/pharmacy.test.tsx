import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, expect, it, vi } from "vitest";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { useLocale } from "../../i18n/LocaleProvider";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { navFor } from "../shell/nav";
import { PharmacyPage } from "./PharmacyPage";

afterEach(() => vi.restoreAllMocks());

const item = {
  item_code: "S-1",
  item_name: "Synthetic item",
  stock_uom: "box",
  is_stock_item: true,
};
function Language() {
  const { setLocale } = useLocale();
  return (
    <button type="button" onClick={() => setLocale("en")}>
      English
    </button>
  );
}
function Refresh() {
  const { refresh } = useSession();
  return (
    <button type="button" onClick={() => void refresh()}>
      Refresh session
    </button>
  );
}
function setup(body: Record<string, unknown>, status = 200) {
  server.use(
    http.get("*/api/live/synthetic-stock", () =>
      HttpResponse.json(body, { status }),
    ),
  );
  renderWithFixtures(
    <>
      <Language />
      <PharmacyPage />
    </>,
    { role: "PHA", sessionMode: "live" },
  );
}

it("shows live items read-only with Thai and English labels", async () => {
  setup({ items: [item, { ...item, item_code: "S-2", is_stock_item: false }] });
  const table = await screen.findByRole("table");
  expect(within(table).getAllByText("Synthetic item")).toHaveLength(2);
  expect(within(table).getByText("สินค้าสต็อก")).toBeVisible();
  expect(within(table).getByText("สินค้าไม่เก็บสต็อก")).toBeVisible();
  expect(within(table).queryByRole("button")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "English" }));
  expect(
    screen.getByRole("columnheader", { name: "Stock unit" }),
  ).toBeVisible();
  expect(screen.getByText("Non-stock item")).toBeVisible();
});

it("shows empty inventory", async () => {
  setup({ items: [] });
  expect(await screen.findByText("ไม่มีรายการสินค้า")).toBeVisible();
});

it.each([
  { items: [{ ...item, is_stock_item: "true" }] },
  { items: [{ ...item, extra: "unexpected" }] },
  { items: [item, item] },
  { items: [{ ...item, item_name: "" }] },
  {},
])("rejects invalid DTO without rendering rows", async (body) => {
  setup(body);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("Synthetic item")).toBeNull();
});

it("shows denied response without fixture fallback", async () => {
  setup({}, 403);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
});

it("refreshes session instead of retrying expired stock access", async () => {
  let expired = false;
  const check = vi.fn();
  const read = vi.fn(() => {
    expired = true;
    return HttpResponse.json({}, { status: 401 });
  });
  renderWithFixtures(<PharmacyPage />, { role: "PHA", sessionMode: "live" });
  server.use(
    http.get("*/api/live/synthetic-stock", read),
    http.get("*/api/session", () => {
      check();
      return expired
        ? HttpResponse.json({}, { status: 401 })
        : HttpResponse.json({
            mode: "live",
            sessionEpoch: "one",
            user: { id: "one", displayName: "one", roles: ["PHA"] },
            capabilities: {},
          });
    }),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /ลองใหม่|retry/i }));
  await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
  expect(read).toHaveBeenCalledTimes(1);
});

it("isolates stock rows across session identities", async () => {
  let epoch = "one";
  renderWithFixtures(
    <>
      <Refresh />
      <PharmacyPage />
    </>,
    { role: "PHA", sessionMode: "live" },
  );
  server.use(
    http.get("*/api/session", () =>
      HttpResponse.json({
        mode: "live",
        sessionEpoch: epoch,
        user: { id: epoch, displayName: epoch, roles: ["PHA"] },
        capabilities: {},
      }),
    ),
    http.get("*/api/live/synthetic-stock", () =>
      HttpResponse.json({ items: [{ ...item, item_name: `Stock ${epoch}` }] }),
    ),
  );
  expect(await screen.findByText("Stock one")).toBeVisible();
  epoch = "two";
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  expect(await screen.findByText("Stock two")).toBeVisible();
  expect(screen.queryByText("Stock one")).toBeNull();
});

it.each(["live", "fixture"] as const)(
  "denies non-PHA in %s mode without showing samples or fetching stock",
  async (sessionMode) => {
    const requests = vi.spyOn(globalThis, "fetch");
    renderWithFixtures(
      <RequireSession>
        <PharmacyPage />
      </RequireSession>,
      { role: "DOC", sessionMode, path: ROUTES.pharmacy },
    );
    expect(await screen.findByText("ไม่มีสิทธิ์เข้าถึงหน้านี้")).toBeVisible();
    expect(screen.queryByTestId("sample-banner")).toBeNull();
    expect(requests.mock.calls.map(([url]) => url)).not.toContain(
      "/api/live/synthetic-stock",
    );
  },
);

it("shows the labeled sample dashboard for fixture PHA without fetching live stock", async () => {
  const requests = vi.spyOn(globalThis, "fetch");
  renderWithFixtures(<PharmacyPage />, { role: "PHA", sessionMode: "fixture" });
  const banner = await screen.findByTestId("sample-banner");
  expect(
    within(banner).getByText(
      "ข้อมูลตัวอย่างในเครื่อง · ยังไม่ยืนยันสัญญาเซิร์ฟเวอร์",
    ),
  ).toBeVisible();
  const movements = screen.getByRole("region", { name: "การเคลื่อนไหวล่าสุด" });
  expect(within(movements).getByText("หน้ากากอนามัย (กล่อง)")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "รายการสินค้า" })).toBeNull();
  expect(screen.queryByText("ไม่มีสิทธิ์เข้าถึงหน้านี้")).toBeNull();
  expect(requests.mock.calls.map(([url]) => url)).not.toContain(
    "/api/live/synthetic-stock",
  );
});

it("advertises pharmacy to pharmacy roles only", () => {
  expect(ROUTES.pharmacy).toBe("/clinic/pharmacy");
  expect(
    navFor(["PHA"], {}).some((item) => item.routeKey === "pharmacy"),
  ).toBe(true);
  expect(
    navFor(["DOC"], {}).some((item) => item.routeKey === "pharmacy"),
  ).toBe(false);
});
