import { useQueryClient } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../test/render";
import { LanguageToggle } from "./LanguageToggle";
import { NotificationsPanel } from "./NotificationsPanel";
import { Topbar } from "./Topbar";

function RefreshItems() {
  const client = useQueryClient();
  return (
    <button
      type="button"
      onClick={() => void client.refetchQueries({ queryKey: ["work-items"] })}
    >
      Refresh items
    </button>
  );
}
const item = {
  id: "item",
  kind: "count",
  labelKey: "nav.today",
  count: 7,
  routeKey: "home",
};

it("renders only canonical visible destinations with allowed capabilities", async () => {
  renderWithFixtures(<NotificationsPanel />, {
    role: "REC",
    caps: { "read.lab": "denied", "read.photo": "blocked_unverified" },
  });
  server.use(
    http.get("/api/read/work-items", () =>
      HttpResponse.json({
        items: [
          item,
          { ...item, id: "role-hidden", routeKey: "system" },
          { ...item, id: "denied", requiredCapability: "read.lab" },
          { ...item, id: "blocked", requiredCapability: "read.photo" },
          { ...item, id: "unknown", routeKey: "https://evil.test" },
          { ...item, id: "prototype", routeKey: "constructor" },
          {
            ...item,
            id: "unknown-capability",
            requiredCapability: "read.unverified",
          },
          { ...item, id: "invalid-count", count: -1 },
          { ...item, id: "private-label", labelKey: "private-patient-name" },
          { ...item, id: "missing", routeKey: undefined },
        ],
        total: 10,
      }),
    ),
  );
  expect(await screen.findByRole("link", { name: /วันนี้/ })).toHaveAttribute(
    "href",
    ROUTES.home,
  );
  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(screen.getByText("7")).toBeVisible();
});

it("shows localized empty state without count, dismissal or persistence", async () => {
  renderWithFixtures(
    <>
      <LanguageToggle />
      <NotificationsPanel />
    </>,
    { role: "REC", workItems: "empty" },
  );
  expect(await screen.findByText("ไม่มีงานค้างสำหรับบทบาทนี้")).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(screen.getByText("No pending work for this role")).toBeVisible();
  expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
});

it("hides cached items after refetch error and retries GET without fabricated counts", async () => {
  renderWithFixtures(
    <>
      <RefreshItems />
      <NotificationsPanel />
    </>,
    { role: "ADM" },
  );
  server.use(
    http.get("/api/read/work-items", () =>
      HttpResponse.json({ items: [item], total: 1 }),
    ),
  );
  await screen.findByText("7");
  server.use(http.get("/api/read/work-items", () => HttpResponse.error()));
  fireEvent.click(screen.getByRole("button", { name: "Refresh items" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ระบบปลายทางไม่พร้อมใช้งาน กรุณาลองใหม่",
  );
  expect(screen.queryByText("7")).toBeNull();
  expect(screen.queryByText("0")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  server.use(
    http.get("/api/read/work-items", () =>
      HttpResponse.json({ items: [item], total: 1 }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "ลองใหม่" }));
  expect(await screen.findByText("7")).toBeVisible();
});

it.each([390, 834, 1440] as const)(
  "notification popover supports focus, internal links, Escape and outside dismissal at %s",
  async (width) => {
    setViewport(width);
    renderWithFixtures(
      <>
        <Topbar />
        <button type="button">Outside</button>
      </>,
      { role: "ADM" },
    );
    const user = userEvent.setup();
    const trigger = await screen.findByRole("button", { name: "การแจ้งเตือน" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "การแจ้งเตือน" });
    expect(dialog).toHaveFocus();
    const link = await within(dialog).findByRole("link");
    await act(async () => {
      link.focus();
    });
    expect(dialog).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.click(trigger);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "ปิด" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Outside" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  },
);
