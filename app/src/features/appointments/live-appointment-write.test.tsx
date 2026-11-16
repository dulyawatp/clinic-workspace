import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AppointmentPage } from "./AppointmentPage";
import { ReceptionHomePage } from "../reception/ReceptionHomePage";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const LIST = "/api/method/clinic_ui.api.appointments.synthetic_appointments";
const VIEW = "/api/method/clinic_ui.api.appointments.view";
const DIRECTORY = "/api/method/clinic_ui.api.directory.synthetic_patients";
const ACK = "/api/method/clinic_ui.api.appointments.ack";
const BOOK = "/api/method/clinic_ui.api.appointments.book";
const RESCHEDULE = "/api/method/clinic_ui.api.appointments.reschedule";
const CHECKIN = "/api/method/clinic_ui.api.appointments.checkin";
const TOKEN = "a".repeat(64);
const ALIAS = "appointment-alias-1";

const ROW = {
  workspace_patient_id: "WS-1",
  appointment_id: ALIAS,
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Open",
};

const DETAIL = {
  ...ROW,
  modified: "2026-11-20 09:00:00.000000",
};

function bootstrap(caps: Record<string, string>) {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["REC"] },
    capabilities: { "read.appointment": "read_only", ...caps },
    csrfToken: "csrf",
  };
}

function serveBase(caps: Record<string, string>) {
  server.use(
    http.get(BOOTSTRAP, () => HttpResponse.json({ message: bootstrap(caps) })),
    http.get(LIST, () => HttpResponse.json({ message: { items: [ROW] } })),
    http.get(DIRECTORY, () =>
      HttpResponse.json({
        message: { items: [{ workspace_patient_id: "WS-1", display_name: "Patient One" }] },
      }),
    ),
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: false },
      }),
    ),
  );
}

function writeSuccess(status: string) {
  return HttpResponse.json({
    message: {
      status,
      ackToken: TOKEN,
      appointment: DETAIL,
    },
  });
}

function acknowledge() {
  return HttpResponse.json({ message: { acknowledged: true } });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("shows the booking form only when the server enables booking", async () => {
  serveBase({});
  renderWithFixtures(<ReceptionHomePage />);
  expect(
    await screen.findByText(ALIAS),
  ).toBeVisible();
  const start = screen.getByRole("button", { name: "เริ่มจองนัดทดสอบ" });
  expect(start).toBeDisabled();
  expect(
    screen.getByText("การจองไม่เปิดใช้งานสำหรับบัญชีนี้"),
  ).toBeVisible();
});

it("books a selected readable allowlisted patient and acknowledges once", async () => {
  serveBase({
    "write.appointment.book": "enabled",
    "read.patient": "read_only",
  });
  const book = vi.fn();
  const ack = vi.fn();
  server.use(
    http.post(BOOK, async ({ request }) => {
      book(await request.json());
      return writeSuccess("booked");
    }),
    http.post(ACK, async ({ request }) => {
      ack(await request.json());
      return acknowledge();
    }),
  );
  const user = userEvent.setup();
  renderWithFixtures(<ReceptionHomePage />);

  const start = await screen.findByRole("button", {
    name: "เริ่มจองนัดทดสอบ",
  });
  await waitFor(() => expect(start).toBeEnabled());
  await user.click(start);
  await user.selectOptions(
    await screen.findByLabelText("ผู้ป่วย"),
    "WS-1",
  );
  await user.type(screen.getByLabelText("วันที่"), "2026-11-25");
  await user.type(screen.getByLabelText("เวลา"), "10:15");
  await user.click(screen.getByRole("button", { name: "ยืนยันการจอง" }));

  expect(
    await screen.findByText("จองแล้ว เลขที่อ้างอิง: appointment-alias-1"),
  ).toBeVisible();
  expect(book).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    workspace_patient_id: "WS-1",
    appointment_date: "2026-11-25",
    appointment_time: "10:15",
  });
  expect(ack).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    kind: "book",
    ack_token: TOKEN,
  });
  expect(localStorage.length).toBe(0);
});

it("reschedules a server-eligible appointment using the loaded version", async () => {
  serveBase({ "write.appointment.reschedule": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: true, can_checkin: false },
      }),
    ),
  );
  const reschedule = vi.fn();
  const ack = vi.fn();
  server.use(
    http.post(RESCHEDULE, async ({ request }) => {
      reschedule(await request.json());
      return writeSuccess("rescheduled");
    }),
    http.post(ACK, async ({ request }) => {
      ack(await request.json());
      return acknowledge();
    }),
  );
  const user = userEvent.setup();
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );

  await user.click(
    await screen.findByRole("button", { name: "เลื่อนนัด" }),
  );
  const date = screen.getByLabelText("วันที่");
  await user.clear(date);
  await user.type(date, "2026-11-26");
  await user.click(
    screen.getByRole("button", { name: "ยืนยันเวลาใหม่" }),
  );

  expect(
    await screen.findByText(/เลื่อนนัดเป็น 2026-11-20 เวลา 09:30:00/),
  ).toBeVisible();
  expect(reschedule).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    workspace_appointment_id: ALIAS,
    base_modified: DETAIL.modified,
    appointment_date: "2026-11-26",
    appointment_time: "09:30",
  });
  expect(ack).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    kind: "reschedule",
    ack_token: TOKEN,
  });
});

it("reports a stale version conflict and keeps the appointment lock", async () => {
  serveBase({ "write.appointment.reschedule": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: true, can_checkin: false },
      }),
    ),
    http.post(RESCHEDULE, () =>
      HttpResponse.json({ message: { code: "STALE_VERSION" } }, { status: 409 }),
    ),
  );
  const user = userEvent.setup();
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );

  await user.click(
    await screen.findByRole("button", { name: "เลื่อนนัด" }),
  );
  await user.click(
    screen.getByRole("button", { name: "ยืนยันเวลาใหม่" }),
  );

  expect(
    await screen.findByText(/นัดหมายนี้เปลี่ยนไปหลังจากโหลดข้อมูล/),
  ).toBeVisible();
  expect(localStorage.getItem("clinic.appointment-write.pending")).toBe("1");
  expect(localStorage.getItem("clinic.patient-write.pending")).toBeNull();
});

it("offers check-in only when the server detail flag is true", async () => {
  serveBase({ "write.appointment.checkin": "enabled" });
  renderWithFixtures(<ReceptionHomePage />);
  expect(await screen.findByText(ALIAS)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "รับเข้าตรวจ" }),
  ).toBeNull();
  expect(
    await screen.findByText("ไม่เข้าเงื่อนไขสำหรับการรับเข้าตรวจ"),
  ).toBeVisible();
});

it("checks in an eligible row through an explicit confirmation", async () => {
  serveBase({ "write.appointment.checkin": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: true },
      }),
    ),
  );
  const checkin = vi.fn();
  const ack = vi.fn();
  server.use(
    http.post(CHECKIN, async ({ request }) => {
      checkin(await request.json());
      return writeSuccess("checked_in");
    }),
    http.post(ACK, async ({ request }) => {
      ack(await request.json());
      return acknowledge();
    }),
  );
  const user = userEvent.setup();
  renderWithFixtures(<ReceptionHomePage />);

  await user.click(
    await screen.findByRole("button", { name: "รับเข้าตรวจ" }),
  );
  const group = screen.getByRole("group", {
    name: "ยืนยันการรับเข้าตรวจ",
  });
  await user.click(
    within(group).getByRole("button", { name: "รับเข้าตรวจ" }),
  );

  expect(await screen.findByText("รับเข้าตรวจแล้ว")).toBeVisible();
  expect(checkin).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    workspace_appointment_id: ALIAS,
    base_modified: DETAIL.modified,
  });
  expect(ack).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    kind: "checkin",
    ack_token: TOKEN,
  });
});

it.each([390, 834, 1440])(
  "keeps a long workspace reference intact and wrappable at %i px",
  async (width) => {
    setViewport(width as 390 | 834 | 1440);
    serveBase({});
    const longAlias = "appointment-alias-".repeat(12);
    server.use(
      http.get(VIEW, () =>
        HttpResponse.json({
          message: {
            ...DETAIL,
            appointment_id: longAlias,
            can_reschedule: false,
            can_checkin: false,
          },
        }),
      ),
    );
    renderWithFixtures(
      <Routes>
        <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
      </Routes>,
      { path: `/clinic/appointments/${longAlias}` },
    );
    const reference = await screen.findByTestId("appointment-reference");
    expect(reference).toHaveTextContent(longAlias);
    expect(reference).toHaveStyle({
      overflowWrap: "anywhere",
      minWidth: "0",
    });
    expect(reference.parentElement).toHaveClass("appointment-patient-id");
    expect(reference.parentElement).toHaveStyle({ minWidth: "0" });
  },
);

it("renders cancellation and no-show as unavailable even when capability payload says enabled", async () => {
  serveBase({
    "write.appointment.book": "enabled",
    "write.appointment.reschedule": "enabled",
    "write.appointment.checkin": "enabled",
    "write.appointment.cancel": "enabled",
  });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: true, can_checkin: true },
      }),
    ),
  );
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
  await screen.findByRole("button", { name: "เลื่อนนัด" });
  expect(screen.getByRole("button", { name: /ยกเลิก|cancel/i })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: /ไม่มา|no-show/i }),
  ).toBeDisabled();
  expect(
    screen.getByText(
      /ไม่มีในสัญญานี้|not available in this contract/i,
    ),
  ).toBeVisible();
});

it("keeps reschedule read-only when the server did not enable it", async () => {
  serveBase({ "write.appointment.reschedule": "read_only" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: true, can_checkin: false },
      }),
    ),
  );
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
  expect(
    await screen.findByRole("button", { name: "เลื่อนนัด" }),
  ).toBeDisabled();
});

it("does not fetch or show check-in when the capability is not enabled", async () => {
  serveBase({ "write.appointment.checkin": "read_only" });
  const detail = vi.fn();
  server.use(
    http.get(VIEW, () => {
      detail();
      return HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: true },
      });
    }),
  );
  renderWithFixtures(<ReceptionHomePage />);
  await screen.findByText(ALIAS);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "รับเข้าตรวจ" }),
    ).toBeNull(),
  );
  expect(detail).not.toHaveBeenCalled();
});

it("keeps the lock visible across a remount after an uncertain acknowledgement", async () => {
  serveBase({ "write.appointment.book": "enabled" });
  server.use(
    http.post(BOOK, () => writeSuccess("booked")),
    http.post(ACK, () =>
      HttpResponse.json({ message: { acknowledged: false } }, { status: 200 }),
    ),
  );
  const user = userEvent.setup();
  const first = renderWithFixtures(<ReceptionHomePage />);
  let start = await screen.findByRole("button", {
    name: "เริ่มจองนัดทดสอบ",
  });
  await waitFor(() => expect(start).toBeEnabled());
  await user.click(start);
  await user.selectOptions(await screen.findByLabelText("ผู้ป่วย"), "WS-1");
  await user.type(screen.getByLabelText("วันที่"), "2026-11-25");
  await user.type(screen.getByLabelText("เวลา"), "10:15");
  await user.click(screen.getByRole("button", { name: "ยืนยันการจอง" }));
  expect(
    (await screen.findAllByText(/การบันทึกนัดหมายก่อนหน้ายังไม่ยืนยัน/)).length,
  ).toBeGreaterThan(0);
  first.unmount();

  renderWithFixtures(<ReceptionHomePage />);
  expect(
    (await screen.findAllByText(/การบันทึกนัดหมายก่อนหน้ายังไม่ยืนยัน/)).length,
  ).toBeGreaterThan(0);
  start = screen.getByRole("button", { name: "เริ่มจองนัดทดสอบ" });
  expect(start).toBeDisabled();
});

it("never falls back to the legacy proxy for live appointment detail", async () => {
  serveBase({});
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
  await screen.findByText(DETAIL.modified);
  const legacy = fetchSpy.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.startsWith("/api/") && !url.startsWith("/api/method/"));
  expect(legacy).toEqual([]);
});

it("fails closed on a malformed detail response", async () => {
  serveBase({});
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          ...DETAIL,
          can_reschedule: false,
          can_checkin: false,
          native_appointment_id: "native-1",
        },
      }),
    ),
  );
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("native-1")).toBeNull();
});
