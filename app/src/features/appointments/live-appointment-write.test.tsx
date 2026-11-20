import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
const BOOK = "/api/resource/Patient%20Appointment";
const LINK = "/api/method/frappe.desk.search.search_link";
const RESCHEDULE = "/api/method/clinic_ui.api.appointments.reschedule";
const CHECKIN = "/api/method/clinic_ui.api.appointments.checkin";
const CANCEL = "/api/method/clinic_ui.api.appointments_cancel_write.cancel";
const NO_SHOW = "/api/method/clinic_ui.api.appointments_cancel_write.no_show";
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

const bookingFields = [
  ["patient", "Patient", "ผู้ป่วย", "PAT-NATIVE", { status: "Active" }],
  ["company", "Company", "บริษัท", "Hospital Co", null],
  ["department", "Medical Department", "แผนก", "General Medicine", null],
  ["appointment_type", "Appointment Type", "ประเภทนัดหมาย", "Department Visit", { allow_booking_for: "Department" }],
] as const;
const nativeBooking = {
  patient: "PAT-NATIVE",
  company: "Hospital Co",
  department: "General Medicine",
  appointment_type: "Department Visit",
  appointment_for: "Department",
  appointment_date: "2026-11-25",
  appointment_time: "10:15",
};
const nativeDoc = { ...nativeBooking, doctype: "Patient Appointment", name: "HLC-APP-0042", status: "Scheduled" };

function serveBooking() {
  serveBase({ "write.appointment.book": "enabled" });
  const lookups = vi.fn();
  function reply(args: Record<string, unknown>) {
    lookups(args);
    const field = bookingFields.find(([name]) => args.link_fieldname === name);
    if (!field) return new HttpResponse(null, { status: 400 });
    return HttpResponse.json({ message: [{ value: field[3], label: "" }] });
  }
  server.use(
    http.get(LINK, ({ request }) => reply(Object.fromEntries(new URL(request.url).searchParams))),
    http.post(LINK, async ({ request }) => reply(await request.json() as Record<string, unknown>)),
  );
  return lookups;
}

async function fillBooking(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "เริ่มจองนัด" }));
  for (const [, , label, id] of bookingFields) {
    await screen.findByRole("option", { name: id });
    await user.selectOptions(screen.getByRole("combobox", { name: label }), id);
  }
  await user.type(screen.getByLabelText("วันที่"), nativeBooking.appointment_date);
  await user.type(screen.getByLabelText("เวลา"), nativeBooking.appointment_time);
  return screen.getByRole("button", { name: "ยืนยันการจอง" });
}

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

function writeSuccess(status: string, appointment = DETAIL) {
  return HttpResponse.json({ message: { status, appointment } });
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
  const start = screen.getByRole("button", { name: "เริ่มจองนัด" });
  expect(start).toBeDisabled();
  expect(
    screen.getByText("การจองไม่เปิดใช้งานสำหรับบัญชีนี้"),
  ).toBeVisible();
});

it("posts exact native Link values once, refreshes appointments and shows the returned identity and status", async () => {
  const lookups = serveBooking();
  const book = vi.fn();
  const list = vi.fn();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  server.use(
    http.get(LIST, () => {
      list();
      return HttpResponse.json({ message: { items: [ROW] } });
    }),
    http.post(BOOK, async ({ request }) => {
      book(await request.json(), request.headers.get("X-Frappe-CSRF-Token"));
      await pending;
      return HttpResponse.json({ data: nativeDoc });
    }),
  );
  const user = userEvent.setup();
  renderWithFixtures(<ReceptionHomePage />);
  const submit = await fillBooking(user);
  const form = submit.closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  await waitFor(() => expect(book).toHaveBeenCalledExactlyOnceWith(nativeBooking, "csrf"));
  expect(submit).toBeDisabled();
  expect(screen.getByLabelText("วันที่")).toBeDisabled();
  release();

  expect(await screen.findByRole("link", { name: nativeDoc.name }))
    .toHaveAttribute("href", `/clinic/appointments/${nativeDoc.name}`);
  expect(screen.getByText(/บันทึกนัดหมายแล้ว/)).toHaveTextContent("HLC-APP-0042 · นัดหมาย");
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  for (const [field, doctype, , , filters] of bookingFields) {
    expect(lookups).toHaveBeenCalledWith(expect.objectContaining({
      doctype, link_fieldname: field, reference_doctype: "Patient Appointment",
      ...(filters ? { filters: field === "patient" ? filters : JSON.stringify(filters) } : {}),
    }));
  }
  expect(localStorage.length).toBe(0);
});

it("offers Link lookup patients even without any fixture appointments", async () => {
  const lookups = serveBooking();
  server.use(
    http.get(LIST, () => HttpResponse.json({ message: { items: [] } })),
  );
  const user = userEvent.setup();
  renderWithFixtures(<ReceptionHomePage />);
  await user.click(await screen.findByRole("button", { name: "เริ่มจองนัด" }));
  expect(await screen.findByRole("option", { name: "PAT-NATIVE" })).toBeInTheDocument();
  await user.type(screen.getByRole("searchbox", { name: "ค้นหาผู้ป่วย" }), "native");
  await waitFor(() => expect(lookups).toHaveBeenCalledWith(expect.objectContaining({ txt: "native", doctype: "Patient" })));
});

it.each([
  ["native duplicate", () => HttpResponse.json({
    exc_type: "DuplicateEntryError",
    exc: "secret traceback",
    _server_messages: JSON.stringify([
      JSON.stringify({ message: "Unrelated warning" }),
      JSON.stringify({
        message: 'Patient already has an appointment <a href="/app/patient-appointment/existing-private">existing-private</a> booked for <b>General Medicine</b> on 25&nbsp;Nov.',
        raise_exception: 1,
      }),
    ]),
  }, { status: 500 }), /Patient already has an appointment booked for General Medicine on 25 Nov/],
  ["permission denied", () => HttpResponse.json({ exc: "secret traceback" }, { status: 403 }), /บัญชีนี้ไม่มีสิทธิ์/],
  ["lost response", () => HttpResponse.error(), /ยังไม่ยืนยันผลการนัดหมาย/],
  ["unverified status", () => HttpResponse.json({ data: { ...nativeDoc, status: "booked" } }), /ยังไม่ยืนยันผลการนัดหมาย/],
  ["missing native ID", () => HttpResponse.json({ data: { ...nativeDoc, name: undefined } }), /ยังไม่ยืนยันผลการนัดหมาย/],
  ["mismatched patient", () => HttpResponse.json({ data: { ...nativeDoc, patient: "OTHER-PRIVATE" } }), /ยังไม่ยืนยันผลการนัดหมาย/],
] as const)("does not retry or show a receipt for %s", async (_, respond, message) => {
  serveBooking();
  const book = vi.fn(respond);
  const fallback = vi.fn(() => HttpResponse.json({}));
  server.use(
    http.post(BOOK, () => book()),
    http.post("/api/method/clinic_ui.api.appointments.book", fallback),
  );
  const user = userEvent.setup();
  renderWithFixtures(<ReceptionHomePage />);
  await user.click(await fillBooking(user));
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.queryByText(/secret traceback|existing-private|Unrelated warning|OTHER-PRIVATE/)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: nativeDoc.name })).not.toBeInTheDocument();
  expect(book).toHaveBeenCalledTimes(1);
  expect(fallback).not.toHaveBeenCalled();
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
  server.use(
    http.post(RESCHEDULE, async ({ request }) => {
      reschedule(await request.json());
      return writeSuccess("rescheduled");
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
});

it("reports a stale version conflict without locking or retrying", async () => {
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
  expect(localStorage.length).toBe(0);
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
  server.use(
    http.post(CHECKIN, async ({ request }) => {
      checkin(await request.json());
      return writeSuccess("checked_in");
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

it("keeps cancellation and no-show capability-gated without posting when not enabled", async () => {
  serveBase({
    "write.appointment.book": "enabled",
    "write.appointment.reschedule": "enabled",
    "write.appointment.checkin": "enabled",
  });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: true, can_checkin: true },
      }),
    ),
  );
  const cancel = vi.fn();
  const noShow = vi.fn();
  server.use(
    http.post(CANCEL, ({ request }) => {
      cancel(request.url);
      return writeSuccess("cancelled", { ...DETAIL, status: "Cancelled" });
    }),
    http.post(NO_SHOW, ({ request }) => {
      noShow(request.url);
      return writeSuccess("no_show", { ...DETAIL, status: "No Show" });
    }),
  );
  const user = userEvent.setup();
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
  await screen.findByRole("button", { name: "เลื่อนนัด" });
  const cancelButton = screen.getByRole("button", { name: /ยกเลิก|cancel/i });
  const noShowButton = screen.getByRole("button", { name: /ไม่มา|no-show/i });
  expect(cancelButton).toBeDisabled();
  expect(noShowButton).toBeDisabled();
  await user.click(cancelButton);
  await user.click(noShowButton);

  expect(cancel).not.toHaveBeenCalled();
  expect(noShow).not.toHaveBeenCalled();
});

it("cancels through the confirmation step, the cancel endpoint without an acknowledgement", async () => {
  serveBase({ "read.patient": "read_only", "write.appointment.cancel": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: false },
      }),
    ),
  );
  const cancel = vi.fn();
  server.use(
    http.post(CANCEL, async ({ request }) => {
      cancel(await request.json());
      return writeSuccess("cancelled", { ...DETAIL, status: "Cancelled" });
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
    await screen.findByRole("button", { name: "ยกเลิกนัด" }),
  );
  const group = screen.getByRole("group", { name: "ยืนยันการยกเลิกนัด" });
  const confirm = within(group).getByRole("button", {
    name: "ยืนยันการยกเลิกนัด",
  });
  expect(confirm).toBeDisabled();
  expect(cancel).not.toHaveBeenCalled();

  await user.type(
    within(group).getByLabelText("เหตุผล (บันทึกไว้ในนัดหมาย)"),
    "ผู้ป่วยโทรขอยกเลิก",
  );
  await user.click(confirm);

  expect(await screen.findByText("ยกเลิกนัดหมายแล้ว")).toBeVisible();
  expect(cancel).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    patient: "WS-1",
    appointment_id: ALIAS,
    reason: "ผู้ป่วยโทรขอยกเลิก",
  });
  expect(localStorage.length).toBe(0);
});

it("records a no-show through its own endpoint without an acknowledgement", async () => {
  serveBase({ "write.appointment.no_show": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: false },
      }),
    ),
  );
  const noShow = vi.fn();
  server.use(
    http.post(NO_SHOW, async ({ request }) => {
      noShow(await request.json());
      return writeSuccess("no_show", { ...DETAIL, status: "No Show" });
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
    await screen.findByRole("button", { name: "ระบุว่าไม่มา" }),
  );
  const group = screen.getByRole("group", { name: "ยืนยันว่าไม่มา" });
  await user.type(
    within(group).getByLabelText("เหตุผล (บันทึกไว้ในนัดหมาย)"),
    "ไม่มาตามนัด",
  );
  await user.click(
    within(group).getByRole("button", { name: "ยืนยันว่าไม่มา" }),
  );

  expect(await screen.findByText("บันทึกว่าไม่มาแล้ว")).toBeVisible();
  expect(noShow).toHaveBeenCalledWith({
    operation_key: expect.any(String),
    patient: "WS-1",
    appointment_id: ALIAS,
    reason: "ไม่มาตามนัด",
  });
});

it("reports an unknown cancel outcome and never retries automatically", async () => {
  serveBase({ "write.appointment.cancel": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: false },
      }),
    ),
  );
  const cancel = vi.fn();
  server.use(
    http.post(CANCEL, () => {
      cancel();
      return HttpResponse.json({}, { status: 500 });
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
    await screen.findByRole("button", { name: "ยกเลิกนัด" }),
  );
  const group = screen.getByRole("group", { name: "ยืนยันการยกเลิกนัด" });
  await user.type(
    within(group).getByLabelText("เหตุผล (บันทึกไว้ในนัดหมาย)"),
    "ผู้ป่วยโทรขอยกเลิก",
  );
  await user.click(
    within(group).getByRole("button", { name: "ยืนยันการยกเลิกนัด" }),
  );

  expect(
    await screen.findByText(/ยังไม่ยืนยันผลการนัดหมาย/),
  ).toBeVisible();
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0);
});

it("offers no cancel or no-show action on a terminal appointment", async () => {
  serveBase({
    "write.appointment.cancel": "enabled",
    "write.appointment.no_show": "enabled",
  });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          ...DETAIL,
          status: "Cancelled",
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
    { path: `/clinic/appointments/${ALIAS}` },
  );

  expect(
    await screen.findByText(/อยู่ในสถานะสิ้นสุดแล้ว/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "ยกเลิกนัด" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "ระบุว่าไม่มา" }),
  ).toBeNull();
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

it("does not carry a stop state across a remount", async () => {
  serveBooking();
  server.use(
    http.post(BOOK, () => HttpResponse.json({}, { status: 500 })),
  );
  const user = userEvent.setup();
  const first = renderWithFixtures(<ReceptionHomePage />);
  await user.click(await fillBooking(user));
  expect(
    await screen.findByText(/ยังไม่ยืนยันผลการนัดหมาย/),
  ).toBeVisible();
  expect(localStorage.length).toBe(0);
  first.unmount();

  renderWithFixtures(<ReceptionHomePage />);
  const start = await screen.findByRole("button", { name: "เริ่มจองนัด" });
  await waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByText(/ยังไม่ยืนยันผลการนัดหมาย/)).toBeNull();
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

it("accepts native identifier values in the canonical fields", async () => {
  serveBase({});
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          workspace_patient_id: "PAT-0001",
          appointment_id: ALIAS,
          appointment_date: DETAIL.appointment_date,
          appointment_time: DETAIL.appointment_time,
          status: DETAIL.status,
          modified: DETAIL.modified,
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
    { path: `/clinic/appointments/${ALIAS}` },
  );
  expect(await screen.findByTestId("appointment-reference")).toHaveTextContent(
    ALIAS,
  );
  expect(screen.getByText("PAT-0001")).toBeVisible();
  expect(screen.getByText(DETAIL.modified)).toBeVisible();
});

it("rejects a detail with a raw alternate identifier field", async () => {
  serveBase({});
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          ...DETAIL,
          native_appointment_id: "APT-0001",
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
    { path: `/clinic/appointments/${ALIAS}` },
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("APT-0001")).toBeNull();
});

it("fails closed on a malformed detail response", async () => {
  serveBase({});
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          ...DETAIL,
          modified: "not-a-version",
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
    { path: `/clinic/appointments/${ALIAS}` },
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("not-a-version")).toBeNull();
});
