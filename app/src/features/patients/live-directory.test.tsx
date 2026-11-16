import { useQueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";
import { useSession } from "../../app/SessionProvider";
import { ROUTES } from "../../app/routes";
import { useLocale } from "../../i18n/LocaleProvider";
import { createHandlers } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { PatientDirectoryPage } from "./PatientDirectoryPage";

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
function Refetch() {
  const client = useQueryClient();
  return (
    <button
      type="button"
      onClick={() =>
        void client.invalidateQueries({ queryKey: ["live-directory"] })
      }
    >
      Refetch
    </button>
  );
}

function setup(
  body: Record<string, unknown>,
  status = 200,
  role: "DOC" | "REC" | "NUR" = "DOC",
) {
  renderWithFixtures(<PatientDirectoryPage />, {
    role,
    sessionMode: "live",
  });
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json(body, { status }),
    ),
  );
}

it.each(["DOC", "REC", "NUR"] as const)(
  "shows only live names and workspace references in an accessible table for %s",
  async (role) => {
    setup(
      {
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Live Example",
          },
        ],
      },
      200,
      role,
    );
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Live Example")).toBeVisible();
    expect(within(table).getByText("workspace-1")).toBeVisible();
    expect(
      within(table).getByText("Live Example").closest("td"),
    ).toHaveAttribute("data-label", "ผู้ป่วย");
    expect(within(table).getByText("workspace-1")).toHaveAttribute(
      "data-label",
      "รหัสอ้างอิงพื้นที่ทำงาน",
    );
    expect(screen.queryByText("native-1")).toBeNull();
    expect(screen.queryByText("สมชาย ใจดี")).toBeNull();
    const open = within(table).getByRole("link", {
      name: /เปิดระเบียน|open record/i,
    });
    expect(open).toHaveAttribute("href", ROUTES.patient("workspace-1"));
    expect(
      screen.queryByRole("button", { name: /ลงทะเบียน|register/i }),
    ).toBeNull();
  },
);

it("shows an empty state without fixture rows", async () => {
  setup({ items: [] });
  expect(await screen.findByText("ไม่มีผู้ป่วยที่ได้รับอนุญาตให้แสดง")).toBeVisible();
  expect(screen.queryByText("สมชาย ใจดี")).toBeNull();
});

it.each([
  [{ items: [{ display_name: "Leaked" }] }, 200],
  [{}, 403],
])("fails closed for invalid or denied response", async (body, status) => {
  setup(body, status);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("Leaked")).toBeNull();
  expect(screen.queryByText("สมชาย ใจดี")).toBeNull();
});

it.each(["DOC", "REC", "NUR"] as const)(
  "translates live directory labels to English for %s",
  async (role) => {
    renderWithFixtures(
      <>
        <Language />
        <PatientDirectoryPage />
      </>,
      { role, sessionMode: "live" },
    );
    server.use(
      http.get("*/api/live/synthetic-patients", () =>
        HttpResponse.json({
          items: [
            {
              workspace_patient_id: "ref",
              native_patient_id: "native",
              display_name: "Live Example",
            },
          ],
        }),
      ),
    );
    expect(
      await screen.findByRole("columnheader", { name: "รหัสอ้างอิงพื้นที่ทำงาน" }),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "English" }));
    expect(
      await screen.findByRole("columnheader", { name: "Workspace reference" }),
    ).toBeVisible();
    expect(
      screen.getByText(/Patient profiles and registration are not available/),
    ).toBeVisible();
  },
);

it("filters and paginates live rows with keyboard controls", async () => {
  const items = Array.from({ length: 11 }, (_, index) => ({
    workspace_patient_id: `ref-${index}`,
    native_patient_id: `native-${index}`,
    display_name: `Patient ${index}`,
  }));
  setup({ items });
  expect(await screen.findByText("Patient 0")).toBeVisible();
  const next = screen.getByRole("button", { name: /ถัดไป|next/i });
  next.focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByText("Patient 10")).toBeVisible();
  await userEvent.type(screen.getByRole("searchbox"), "ref-1");
  expect(await screen.findByText("Patient 1")).toBeVisible();
  expect(screen.queryByText("Patient 0")).toBeNull();
});

it("isolates rows when session identity and mode change", async () => {
  let mode: "live" | "fixture" = "live";
  let epoch = "one";
  let name = "First live patient";
  const fixtureReads = vi.fn();
  renderWithFixtures(
    <>
      <Refresh />
      <PatientDirectoryPage />
    </>,
    { role: "DOC", sessionMode: "live" },
  );
  server.use(
    http.get("*/api/session", () =>
      HttpResponse.json({
        mode,
        sessionEpoch: epoch,
        user: { id: epoch, displayName: epoch, roles: ["DOC"] },
        capabilities: { "read.patient": "enabled" },
      }),
    ),
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: epoch,
            native_patient_id: "native",
            display_name: name,
          },
        ],
      }),
    ),
    http.get("*/api/read/frontdesk-patients", () => {
      fixtureReads();
      return HttpResponse.json({ items: [], total: 0 });
    }),
  );
  expect(await screen.findByText("First live patient")).toBeVisible();
  epoch = "two";
  name = "Second live patient";
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  expect(await screen.findByText("Second live patient")).toBeVisible();
  expect(screen.queryByText("First live patient")).toBeNull();
  expect(fixtureReads).not.toHaveBeenCalled();
  mode = "fixture";
  server.use(...createHandlers({ role: "DOC", sessionMode: "fixture" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh session" }),
  );
  expect(await screen.findByText("สมชาย ใจดี")).toBeVisible();
  expect(screen.queryByText("Second live patient")).toBeNull();
});

it.each([403, 404])("does not offer retry for HTTP %s", async (status) => {
  setup({}, status);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("button", { name: /ลองใหม่|retry/i })).toBeNull();
});

it.each([
  {
    items: [
      {
        workspace_patient_id: "ref",
        native_patient_id: "native",
        display_name: "Visible",
      },
      {
        workspace_patient_id: "ref",
        native_patient_id: "other",
        display_name: "Duplicate",
      },
    ],
  },
  {
    items: [
      {
        workspace_patient_id: "ref",
        native_patient_id: "native",
        display_name: "x".repeat(257),
      },
    ],
  },
  {
    items: [
      {
        workspace_patient_id: "ref",
        native_patient_id: "native",
        display_name: "bad\nname",
      },
    ],
  },
  { items: Array.from({ length: 1001 }, () => ({})) },
])("rejects malformed or unreasonable rows", async (body) => {
  setup(body);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
});

it("shows a truthful filtered empty state", async () => {
  setup({
    items: [
      {
        workspace_patient_id: "ref",
        native_patient_id: "native",
        display_name: "Example",
      },
    ],
  });
  await screen.findByText("Example");
  await userEvent.type(screen.getByRole("searchbox"), "missing");
  expect(
    await screen.findByText("ไม่พบผู้ป่วยที่ได้รับอนุญาตซึ่งตรงกับคำค้น"),
  ).toBeVisible();
});

it("clamps page after live data shrinks", async () => {
  let items = Array.from({ length: 11 }, (_, index) => ({
    workspace_patient_id: `ref-${index}`,
    native_patient_id: `native-${index}`,
    display_name: `Patient ${index}`,
  }));
  renderWithFixtures(
    <>
      <Refetch />
      <PatientDirectoryPage />
    </>,
    { role: "DOC", sessionMode: "live" },
  );
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({ items }),
    ),
  );
  await screen.findByText("Patient 0");
  await userEvent.click(screen.getByRole("button", { name: /ถัดไป|next/i }));
  await screen.findByText("Patient 10");
  items = items.slice(0, 1);
  await userEvent.click(screen.getByRole("button", { name: "Refetch" }));
  expect(await screen.findByText("Patient 0")).toBeVisible();
  expect(screen.queryByText("Patient 10")).toBeNull();
  expect(
    screen.getByRole("button", { name: /ก่อนหน้า|previous/i }),
  ).toBeDisabled();
});

it("refreshes session instead of retrying an expired live request", async () => {
  let expired = false;
  const sessionCheck = vi.fn();
  const read = vi.fn(() => {
    expired = true;
    return HttpResponse.json({}, { status: 401 });
  });
  renderWithFixtures(<PatientDirectoryPage />, {
    role: "DOC",
    sessionMode: "live",
  });
  server.use(
    http.get("*/api/live/synthetic-patients", read),
    http.get("*/api/session", () => {
      sessionCheck();
      return expired
        ? HttpResponse.json({}, { status: 401 })
        : HttpResponse.json({
            mode: "live",
            sessionEpoch: "one",
            user: { id: "one", displayName: "one", roles: ["DOC"] },
            capabilities: { "read.patient": "enabled" },
          });
    }),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /ลองใหม่|retry/i }));
  await waitFor(() => expect(sessionCheck).toHaveBeenCalledTimes(2));
  expect(read).toHaveBeenCalledTimes(1);
});

it.each(["PHA", "CAS", "MGR", "ADM"] as const)(
  "never requests live data for %s sessions",
  async (role) => {
    const read = vi.fn(() => HttpResponse.json({ items: [] }));
    renderWithFixtures(<PatientDirectoryPage />, {
      role,
      sessionMode: "live",
      caps: { "read.patient": "enabled" },
    });
    server.use(http.get("*/api/live/synthetic-patients", read));
    expect(
      await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" }),
    ).toBeVisible();
    expect(read).not.toHaveBeenCalled();
  },
);
