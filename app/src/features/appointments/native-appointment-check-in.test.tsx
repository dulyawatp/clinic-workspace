import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import {
  parseNativeAppointmentDetail,
  parseNativeAppointmentRead,
  readNativeAppointmentItem,
} from "../../transport/native/appointment";
import { AppointmentPage } from "./AppointmentPage";

vi.mock("../clinical/NativeLabReports", () => ({ NativeLabReportPanel: () => <p>Lab controls</p> }));

const ALIAS = "sealed-appointment-alias";
const NATIVE_ID = "PA-42 #B";
const DOC = `/api/resource/Patient%20Appointment/${encodeURIComponent(NATIVE_ID)}`;
const LINK = "/api/method/frappe.desk.search.search_link";
const POST = "/api/method/healthcare.healthcare.doctype.patient_appointment.patient_appointment.check_in_appointment";
const OWN_BEFORE = "/api/method/clinic_photos.api.visit_photos.read_own_before";
const PHOTO_OPTIONS = "/api/method/clinic_photos.api.visit_options.capture_options";
const ACTION = "รับเข้าตรวจ";
const CONFIRM = "ยืนยันการรับเข้าตรวจ";
const PRACTITIONER = "HPR-012";
const PRACTITIONER_LABEL = "Dr. Rin";
const PATIENT_NAME = "ริน สุขใจ";
const NATIVE_DOC = {
  name: NATIVE_ID,
  status: "Open",
  appointment_based_on_check_in: 1,
  service_unit: "Exam 3",
};
const DETAIL = {
  appointment_id: ALIAS,
  workspace_patient_id: "WS-1",
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Open",
  modified: "2026-11-20 09:00:00.000000",
  is_fixture: false,
  native_appointment_id: NATIVE_ID,
  can_reschedule: false,
  can_checkin: false,
};

it("parses optional check-in enablement strictly", () => {
  expect(parseNativeAppointmentRead({ ...DETAIL, native_checkin_enabled: false }).native_checkin_enabled).toBe(false);
  expect(parseNativeAppointmentRead({ ...DETAIL, native_checkin_enabled: true }).native_checkin_enabled).toBe(true);
  expect(parseNativeAppointmentRead(DETAIL).native_checkin_enabled).toBeUndefined();
  for (const value of [null, 0, "false", undefined]) {
    expect(() => parseNativeAppointmentRead({ ...DETAIL, native_checkin_enabled: value })).toThrow();
  }
});

it("retains a safe optional name only in native appointment reads", () => {
  const longestName = "𠮷".repeat(140);
  const { is_fixture, native_appointment_id, can_reschedule, can_checkin, ...writeDetail } = DETAIL;
  const { modified, ...listItem } = writeDetail;
  expect(parseNativeAppointmentRead({
    ...DETAIL, patient_display_name: longestName,
  }).patient_display_name).toBe(longestName);
  expect(parseNativeAppointmentRead(DETAIL)).not.toHaveProperty("patient_display_name");
  expect(parseNativeAppointmentRead({
    ...writeDetail, can_reschedule, can_checkin, is_fixture: true, patient_display_name: PATIENT_NAME,
  })).not.toHaveProperty("patient_display_name");
  expect(() => parseNativeAppointmentRead({ ...DETAIL, patient_name: PATIENT_NAME })).toThrow();
  expect(() => parseNativeAppointmentDetail({ ...writeDetail, patient_display_name: PATIENT_NAME })).toThrow();
  expect(() => readNativeAppointmentItem({ ...listItem, patient_display_name: PATIENT_NAME })).toThrow();
});

it("drops malformed optional names without dropping appointment details", () => {
  for (const value of [
    null, undefined, 42, {}, "", "  ", " Rin", "Rin\nSuk", "Rin\u202eSuk",
    "<b>Rin</b>", "XXXXXXXX", "𠮷".repeat(141), DETAIL.workspace_patient_id,
  ]) {
    expect(parseNativeAppointmentRead({ ...DETAIL, patient_display_name: value })).toEqual(DETAIL);
  }
});

function renderDetail(detail: object = DETAIL, role: "REC" | "DOC" = "REC") {
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
      HttpResponse.json({ message: {
        mode: "live",
        sessionEpoch: "check-in-epoch",
        user: { id: "native|staff", displayName: "Staff", roles: [role] },
        capabilities: { "read.appointment": "read_only" },
        csrfToken: "native-csrf",
      } }),
    ),
    http.get("/api/method/clinic_ui.api.appointments.view", ({ request }) =>
      new URL(request.url).searchParams.get("workspace_appointment_id") === ALIAS
        ? HttpResponse.json({ message: detail })
        : new HttpResponse(null, { status: 404 }),
    ),
    http.post(OWN_BEFORE, () => new HttpResponse(null, { status: 404 })),
    http.post(PHOTO_OPTIONS, () => HttpResponse.json({ message: {
      has_active_consent: false, can_grant: false, can_revoke: false, can_capture_before: false, can_capture_after: false,
    } })),
  );
  renderWithFixtures(
    <Routes><Route path="/appointments/:id" element={<AppointmentPage />} /></Routes>,
    { path: `/appointments/${ALIAS}` },
  );
}

async function openCheckIn(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: ACTION }));
  return within(screen.getByRole("dialog", { name: ACTION }));
}

let restoreDialog: () => void;
beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  restoreDialog = installDialogMethods();
});
afterEach(() => {
  cleanup();
  restoreDialog();
  clearNativeCsrfToken();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

it("uses the approved Reception intake order and keeps check-in available without photo consent", async () => {
  localStorage.setItem("clinic.locale", "en");
  server.use(http.get(DOC, () => HttpResponse.json({ data: NATIVE_DOC })));
  renderDetail();
  const intake = await screen.findByRole("region", { name: "Confirm patient & hand off" });
  const verify = within(intake).getByRole("heading", { name: "1 · Verify patient and consent" });
  const before = within(intake).getByRole("heading", { name: "2 · Before image" });
  const checkIn = await within(intake).findByRole("button", { name: "Check in" });
  const lab = screen.getByText("Lab controls");
  const details = screen.getByRole("heading", { name: "Appointment details" });
  for (const [first, second] of [[verify, before], [before, checkIn], [checkIn, lab], [lab, details]] as const) {
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
  expect(checkIn).toBeEnabled();
  expect(within(intake).queryByLabelText("Before photo file")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Patient intake" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "WS-1" })).not.toBeInTheDocument();
  expect(screen.getByText("WS-1")).toBeVisible();
});

it("uses the patient display name only in the Reception identity heading", async () => {
  localStorage.setItem("clinic.locale", "en");
  renderDetail({ ...DETAIL, patient_display_name: PATIENT_NAME, native_checkin_enabled: false });
  const heading = await screen.findByRole("heading", { name: PATIENT_NAME, level: 2 });
  expect(heading).toBeVisible();
  expect(screen.getAllByText(PATIENT_NAME)).toEqual([heading]);
  const identity = within(heading.closest("header")!);
  expect(identity.getByText("Patient reference")).toBeVisible();
  expect(identity.getByText("WS-1")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "WS-1" })).not.toBeInTheDocument();
});

it("keeps the neutral identity heading when the name is unsafe", async () => {
  localStorage.setItem("clinic.locale", "en");
  const unsafeName = "<b>Rin</b>";
  renderDetail({ ...DETAIL, patient_display_name: unsafeName, native_checkin_enabled: false });
  expect(await screen.findByRole("heading", { name: "Patient intake" })).toBeVisible();
  expect(screen.queryByText(unsafeName)).not.toBeInTheDocument();
});

it("keeps the standard detail for non-Reception staff", async () => {
  localStorage.setItem("clinic.locale", "en");
  renderDetail({ ...DETAIL, patient_display_name: PATIENT_NAME }, "DOC");
  expect(await screen.findByRole("heading", { name: "WS-1" })).toBeVisible();
  expect(screen.queryByText(PATIENT_NAME)).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Confirm patient & hand off" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check in" })).not.toBeInTheDocument();
});

it("shows checked-in status on reload without offering another check-in", async () => {
  localStorage.setItem("clinic.locale", "en");
  server.use(http.get(DOC, () => HttpResponse.json({ data: { ...NATIVE_DOC, status: "Checked In" } })));
  renderDetail();
  const intake = await screen.findByRole("region", { name: "Confirm patient & hand off" });
  expect(await within(intake).findByText("Checked in")).toHaveAttribute("role", "status");
  expect(within(intake).queryByRole("button", { name: "Check in" })).not.toBeInTheDocument();
});

it("uses the exact native ID, permission-filtered Link results and CSRF, then verifies saved assignment without repeating", async () => {
  let release!: () => void;
  const waitForPost = new Promise<void>((resolve) => { release = resolve; });
  let saved = false;
  const reads = vi.fn();
  const write = vi.fn();
  const search = vi.fn();
  server.use(
    http.get(DOC, ({ request }) => {
      reads(new URL(request.url).pathname);
      return HttpResponse.json({ data: saved
        ? { ...NATIVE_DOC, status: "Checked In", practitioner: PRACTITIONER }
        : NATIVE_DOC });
    }),
    http.get(LINK, ({ request }) => {
      const params = new URL(request.url).searchParams;
      search(Object.fromEntries(params));
      return HttpResponse.json({ message: params.get("txt") === "Rin"
        ? [{ value: PRACTITIONER, label: PRACTITIONER_LABEL, description: "Permission-filtered result" }]
        : [] });
    }),
    http.post(POST, async ({ request }) => {
      write(await request.json(), request.headers.get("X-Frappe-CSRF-Token"));
      await waitForPost;
      saved = true;
      return HttpResponse.json({ message: { name: NATIVE_ID } });
    }),
  );
  const fetch = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  renderDetail();
  const dialog = await openCheckIn(user);
  const confirm = dialog.getByRole("button", { name: CONFIRM });
  expect(confirm).toBeDisabled();
  await user.type(dialog.getByRole("searchbox"), "Rin");
  await user.click(await dialog.findByRole("button", { name: `${PRACTITIONER_LABEL} ${PRACTITIONER}` }));
  expect(search).toHaveBeenLastCalledWith({
    doctype: "Healthcare Practitioner", txt: "Rin", reference_doctype: "Patient Appointment",
    link_fieldname: "practitioner", page_length: "10",
  });
  expect(confirm).toBeEnabled();
  await user.dblClick(confirm);
  expect(await dialog.findByText("กำลังรับเข้าตรวจและตรวจสอบนัดหมาย…")).toBeVisible();
  expect(confirm).toBeDisabled();
  expect(dialog.getByRole("button", { name: "ปิด" })).toBeDisabled();
  expect(reads).toHaveBeenCalledTimes(1);
  release();
  expect(await screen.findByText("รับเข้าตรวจกับ Dr. Rin แล้ว")).toBeVisible();
  expect(write).toHaveBeenCalledExactlyOnceWith(
    { appointment_id: NATIVE_ID, practitioner: PRACTITIONER }, "native-csrf",
  );
  expect(reads.mock.calls).toEqual([[DOC], [DOC]]);
  expect(fetch.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith("/api/resource/")))
    .toEqual([DOC, DOC]);
  expect(screen.queryByRole("button", { name: ACTION })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it.each(["Open", "Confirmed", "No Show"])("uses Desk eligibility for %s and defaults the assigned practitioner", async (status) => {
  server.use(http.get(DOC, () => HttpResponse.json({
    data: { ...NATIVE_DOC, status, practitioner: PRACTITIONER },
  })));
  const user = userEvent.setup();
  renderDetail({ ...DETAIL, status: "Checked Out" });
  const dialog = await openCheckIn(user);
  expect(dialog.getByText(PRACTITIONER)).toBeVisible();
  expect(dialog.getByRole("button", { name: CONFIRM })).toBeEnabled();
  await user.click(dialog.getByRole("button", { name: "ปิด" }));
  expect(screen.getByRole("button", { name: ACTION })).toHaveFocus();
});

it.each([
  ["production disabled", { ...DETAIL, native_checkin_enabled: false }],
  ["fixture", { ...DETAIL, is_fixture: true, native_appointment_id: undefined }],
  ["missing native ID", { ...DETAIL, native_appointment_id: undefined }],
])("does not read native metadata or offer check-in for %s", async (_, detail) => {
  const fetch = vi.spyOn(globalThis, "fetch");
  renderDetail({ ...detail, can_checkin: true });
  expect(await screen.findByTestId("appointment-reference")).toHaveTextContent(ALIAS);
  expect(screen.queryByRole("button", { name: ACTION })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith("/api/resource/"))).toBe(false);
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith(LINK))).toBe(false);
});

it.each([
  ["unchecked flag", { ...NATIVE_DOC, appointment_based_on_check_in: 0 }],
  ["ineligible status", { ...NATIVE_DOC, status: "Checked In" }],
])("does not let the legacy flag override %s", async (_, doc) => {
  const read = vi.fn();
  server.use(http.get(DOC, () => { read(); return HttpResponse.json({ data: doc }); }));
  renderDetail({ ...DETAIL, can_checkin: true });
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: ACTION })).not.toBeInTheDocument();
});

it("does not offer the action when Frappe denies the appointment read", async () => {
  server.use(http.get(DOC, () => new HttpResponse(null, { status: 403 })));
  renderDetail();
  expect(await screen.findByText("ไม่สามารถยืนยันเงื่อนไขการรับเข้าตรวจได้")).toBeVisible();
  expect(screen.queryByRole("button", { name: ACTION })).not.toBeInTheDocument();
});

it("keeps submit disabled when the native lookup denies access", async () => {
  server.use(
    http.get(DOC, () => HttpResponse.json({ data: NATIVE_DOC })),
    http.get(LINK, () => new HttpResponse(null, { status: 403 })),
  );
  renderDetail();
  const dialog = await openCheckIn(userEvent.setup());
  expect(await dialog.findByRole("alert")).toHaveTextContent("บัญชีนี้ไม่มีสิทธิ์ค้นหาผู้ตรวจ");
  expect(dialog.getByRole("button", { name: CONFIRM })).toBeDisabled();
});

it.each([
  ["write denied", 403, {}, "บัญชีของคุณไม่สามารถเปลี่ยนแปลงนัดหมายนี้ได้"],
  ["lost response", 0, {}, "ยังยืนยันการรับเข้าตรวจไม่ได้"],
  ["wrong status", 200, { status: "Open" }, "ยังยืนยันการรับเข้าตรวจไม่ได้"],
  ["wrong record", 200, { name: ALIAS }, "ยังยืนยันการรับเข้าตรวจไม่ได้"],
  ["wrong practitioner", 200, { practitioner: "HPR-other" }, "ยังยืนยันการรับเข้าตรวจไม่ได้"],
  ["readback denied", 200, null, "ยังยืนยันการรับเข้าตรวจไม่ได้"],
])("surfaces %s without repeating the POST", async (_, status, overrides, feedback) => {
  let attempted = false;
  const reads = vi.fn();
  const write = vi.fn();
  server.use(
    http.get(DOC, () => {
      reads();
      if (attempted && overrides === null) return new HttpResponse(null, { status: 403 });
      return HttpResponse.json({ data: {
        ...NATIVE_DOC,
        practitioner: PRACTITIONER,
        ...(attempted ? { status: "Checked In", ...overrides } : {}),
      } });
    }),
    http.post(POST, async ({ request }) => {
      write(await request.json());
      attempted = true;
      return status ? new HttpResponse(null, { status }) : HttpResponse.error();
    }),
  );
  const user = userEvent.setup();
  renderDetail();
  const dialog = await openCheckIn(user);
  await user.click(dialog.getByRole("button", { name: CONFIRM }));
  expect(await screen.findByRole("alert")).toHaveTextContent(feedback);
  expect(write).toHaveBeenCalledExactlyOnceWith({ appointment_id: NATIVE_ID, practitioner: PRACTITIONER });
  expect(reads).toHaveBeenCalledTimes(status === 200 ? 2 : 1);
  expect(screen.queryByRole("button", { name: ACTION })).not.toBeInTheDocument();
});
