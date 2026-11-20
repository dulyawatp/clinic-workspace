import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";

const METHODS = "/api/method/clinic_ui.api.lab_reports.";
const REF = "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_abcdefghijklmnop";
const ENC = "ENC-LAB-TEST";
const PAT = "PAT-LAB-TEST";
const receipt = {
  id: "F-LAB-1", name: "Synthetic report.pdf", sizeBytes: 9, uploadedBy: "mock-reception",
  uploadedAt: "2026-11-20 09:07:00.000001", downloadUrl: "/private/files/Synthetic%20report.pdf?fid=F-LAB-1",
};
const appointment = {
  workspace_patient_id: PAT, appointment_id: REF, appointment_date: "2026-11-20", appointment_time: "09:30:00",
  status: "Open", modified: "2026-11-20 09:00:00.000001", can_reschedule: false, can_checkin: false, is_fixture: false,
};
const context = { appointment: "APT-NATIVE", patient: PAT, practitioner: "PRAC-REAL", status: "Closed" };
const encounter = { id: ENC, modified: appointment.modified, docstatus: 0, symptoms: [], diagnoses: [] };
const reply = (message: unknown) => HttpResponse.json({ message });
const list = (items: unknown[] = [], nextOffset: number | null = null) => reply({ items, nextOffset });
const path = ROUTES.appointment(REF);
const linked = ROUTES.linkedVisit(REF, ENC);

function renderPage(initial = path, role: "REC" | "DOC" | "NUR" = "REC") {
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, { "read.patient": "enabled", "read.appointment": "read_only" })));
  return renderWithDataRouter(appRoutes, { initialEntries: [initial], role, sessionMode: "live" });
}

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  sessionStorage.clear();
  server.use(
    http.get("/api/method/clinic_ui.api.appointments.view", () => reply(appointment)),
    http.get("/api/method/clinic_ui.api.encounters.open_for_appointment", () => reply({
      context, encounter, notes: { state: "available", items: [], next_offset: null },
      medications: { state: "available", complete: true, items: [] },
    })),
    http.get("/api/method/clinic_ui.api.encounters.list_for_patient", () => list([{
      id: ENC, patientId: PAT, patientName: "Native patient", practitioner: context.practitioner,
      encounterDate: appointment.appointment_date, docstatus: 0,
    }])),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  localStorage.removeItem(LOCALE_KEY);
  sessionStorage.clear();
});

// MSW/Node request cloning can truncate jsdom multipart streams. Capture the
// actual fetch init for uploads; list and navigation reads still use MSW.
function interceptUpload(handler: (init: RequestInit) => Response | Promise<Response>) {
  const fetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) =>
    input === `${METHODS}upload` && init?.method === "POST" ? Promise.resolve().then(() => handler(init)) : fetch(input, init));
}

const fileText = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onerror = reject;
  reader.onload = () => resolve(String(reader.result));
  reader.readAsText(file);
});

async function chooseReport() {
  const user = userEvent.setup();
  await user.upload(await screen.findByLabelText("Lab report file"),
    new File(["%PDF-test"], receipt.name, { type: "application/pdf" }));
  return screen.getByRole("button", { name: "Upload lab report" });
}

async function verifyReport() {
  expect(await screen.findByText(receipt.name)).toBeVisible();
  expect(screen.getByText(receipt.uploadedBy)).toBeVisible();
  expect(screen.getByText(receipt.uploadedAt)).toBeVisible();
  expect(screen.getByText("9 bytes")).toBeVisible();
  expect(screen.getByRole("link", { name: `View ${receipt.name}` })).toHaveAttribute("href", receipt.downloadUrl);
}

it("uploads one native appointment File and shows the same report across appointment, encounter, and expanded timeline", async () => {
  const posted = vi.fn();
  const listReads = vi.fn();
  const files: typeof receipt[] = [];
  server.use(
    http.get(`${METHODS}list_for_appointment`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      listReads("appointment", params.get("appointment_ref"), params.get("offset"));
      return list(files);
    }),
    http.get(`${METHODS}list_for_encounter`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      listReads("encounter", params.get("encounter_id"), params.get("offset"));
      return list(files);
    }),
  );
  interceptUpload(async (init) => {
    const form = init.body as FormData;
    const file = form.get("file") as File;
    posted([...form.keys()], form.get("appointment_ref"), file.name, await fileText(file), new Headers(init.headers).get("X-Frappe-CSRF-Token"));
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
    files.push(receipt);
    return reply(receipt);
  });
  const appointmentPage = renderPage();
  expect(await screen.findByText("No lab reports attached.")).toBeVisible();
  const button = await chooseReport();
  act(() => { fireEvent.click(button); fireEvent.click(button); });
  expect(await screen.findByText("Lab report uploaded.")).toBeVisible();
  await verifyReport();
  expect(posted.mock.calls).toEqual([[["appointment_ref", "file"], REF, receipt.name, "%PDF-test", "native-csrf"]]);
  expect(document.querySelector("iframe, object, embed")).toBeNull();
  appointmentPage.unmount();

  const visitPage = renderPage(linked, "DOC");
  await verifyReport();
  expect(screen.queryByLabelText("Lab report file")).not.toBeInTheDocument();
  visitPage.unmount();

  renderPage(ROUTES.patientHistory(PAT), "NUR");
  expect(await screen.findByRole("heading", { name: `Visit ${ENC}` })).toBeVisible();
  expect(screen.queryByRole("link", { name: `Visit ${ENC}` })).not.toBeInTheDocument();
  expect(screen.queryByText(receipt.name)).not.toBeInTheDocument();
  expect(listReads.mock.calls.filter(([kind]) => kind === "encounter")).toHaveLength(1);
  await userEvent.setup().click(screen.getByText("Lab reports", { selector: "summary" }));
  await verifyReport();
  expect(screen.queryByLabelText("Lab report file")).not.toBeInTheDocument();
  expect(listReads.mock.calls).toEqual([
    ["appointment", REF, "0"], ["appointment", REF, "0"],
    ["encounter", ENC, "0"], ["encounter", ENC, "0"],
  ]);
  expect(posted).toHaveBeenCalledTimes(1);
});

it("does not launch or load lab reports on a known fixture appointment", async () => {
  const item = vi.fn();
  server.use(
    http.get("/api/method/clinic_ui.api.appointments.view", () => reply({ ...appointment, is_fixture: true })),
    http.get(`${METHODS}list_for_appointment`, () => { item(); return list([receipt]); }),
  );
  renderPage();
  expect(await screen.findByTestId("appointment-reference")).toBeVisible();
  expect(screen.queryByRole("link", { name: "Open encounter" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Lab report file")).not.toBeInTheDocument();
  expect(item).not.toHaveBeenCalled();
});

it.each([
  [403, "Marley denied access to these lab reports."],
  [503, "Lab reports are unavailable here."],
  [200, "No lab reports attached."],
])("distinguishes lab list HTTP %s from other file states", async (status, message) => {
  server.use(http.get(`${METHODS}list_for_encounter`, () => status === 200 ? list() : new HttpResponse(null, { status })));
  renderPage(linked, "DOC");
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.queryByRole("link", { name: /^View / })).not.toBeInTheDocument();
});

it.each([
  "https://evil.invalid/private/files/report.pdf?fid=F-LAB-1",
  "//evil.invalid/private/files/report.pdf?fid=F-LAB-1",
  "/private/files/report.pdf",
  "/private/files/report.pdf?fid=other",
  "/private/files/report.pdf?fid=F-LAB-1&fid=other",
  "/private/files/report.pdf?fid=F-LAB-1&extra=1",
  "/private/files/%2E%2E?fid=F-LAB-1",
  "/private/files/a%2Freport.pdf?fid=F-LAB-1",
  "/private/files/report.pdf?fid=F-LAB-1#fragment",
])("does not render an invalid native lab URL: %s", async (downloadUrl) => {
  server.use(http.get(`${METHODS}list_for_encounter`, () => list([{ ...receipt, downloadUrl }])));
  renderPage(linked, "DOC");
  expect(await screen.findByText("Lab reports are unavailable here.")).toBeVisible();
  expect(screen.queryByText(receipt.name)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: `View ${receipt.name}` })).not.toBeInTheDocument();
});

it("locks a lost upload, only refreshes by GET, and does not infer success from a matching list item", async () => {
  const saved = vi.fn();
  const reads = vi.fn();
  server.use(
    http.get(`${METHODS}list_for_appointment`, () => { reads(); return list(saved.mock.calls.length ? [receipt] : []); }),
  );
  interceptUpload(() => { saved(); throw new TypeError("request lost"); });
  const page = renderPage();
  await userEvent.setup().click(await chooseReport());
  expect(await screen.findByText("The upload could not be confirmed. Refresh reports to check Marley; do not upload again here.")).toBeVisible();
  await verifyReport();
  expect(screen.queryByText("Lab report uploaded.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Upload lab report" })).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Refresh reports" }));
  await waitFor(() => expect(reads).toHaveBeenCalledTimes(3));
  page.unmount();
  renderPage();
  await verifyReport();
  expect(screen.getByText("The upload could not be confirmed. Refresh reports to check Marley; do not upload again here.")).toBeVisible();
  expect(saved).toHaveBeenCalledTimes(1);
});

it("does not confirm an upload with a malformed receipt or expose its URL", async () => {
  const upload = vi.fn(() => reply({ ...receipt, downloadUrl: "/private/files/report.pdf?fid=other" }));
  server.use(http.get(`${METHODS}list_for_appointment`, () => list()));
  interceptUpload(upload);
  renderPage();
  await userEvent.setup().click(await chooseReport());
  expect(await screen.findByText("The upload could not be confirmed. Refresh reports to check Marley; do not upload again here.")).toBeVisible();
  expect(screen.queryByText("Lab report uploaded.")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /^View / })).not.toBeInTheDocument();
  expect(upload).toHaveBeenCalledTimes(1);
});

it("rejects a file outside the approved formats without sending it", async () => {
  const upload = vi.fn(() => reply(receipt));
  server.use(http.get(`${METHODS}list_for_appointment`, () => list()));
  interceptUpload(upload);
  renderPage();
  await userEvent.setup({ applyAccept: false }).upload(await screen.findByLabelText("Lab report file"),
    new File(["not a report"], "script.html", { type: "text/html" }));
  expect(await screen.findByText("Choose one non-empty PDF, JPEG, or PNG.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Upload lab report" })).toBeDisabled();
  expect(upload).not.toHaveBeenCalled();
});

it("continues a report list with the server's bounded offset", async () => {
  const offsets = vi.fn();
  server.use(http.get(`${METHODS}list_for_encounter`, ({ request }) => {
    const offset = new URL(request.url).searchParams.get("offset");
    offsets(offset);
    const items = Array.from({ length: 20 }, (_, index) => ({
      ...receipt, id: `F-FIRST-${index}`, name: `First page ${index}.pdf`,
      sizeBytes: 10, uploadedBy: "first-page-owner", uploadedAt: "2026-11-20 10:00:00.000001",
      downloadUrl: `/private/files/first${index}.pdf?fid=F-FIRST-${index}`,
    }));
    return offset === "0" ? list(items, 20) : list([receipt]);
  }));
  renderPage(linked, "DOC");
  await userEvent.setup().click(await screen.findByRole("button", { name: "Load more reports" }));
  await verifyReport();
  expect(screen.getByText("First page 0.pdf")).toBeVisible();
  expect(offsets.mock.calls).toEqual([["0"], ["20"]]);
  expect(screen.queryByRole("button", { name: "Load more reports" })).not.toBeInTheDocument();
});

it.each([
  [403, "Marley did not allow this upload."],
  [413, "The report exceeds the site’s upload limit."],
  [417, "Marley rejected the file. Check its format and the site’s upload limit."],
])("shows a deterministic native upload refusal (HTTP %s)", async (status, message) => {
  server.use(
    http.get(`${METHODS}list_for_appointment`, () => list()),
  );
  interceptUpload(() => new HttpResponse(null, { status }));
  renderPage();
  await userEvent.setup().click(await chooseReport());
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.queryByText("Lab report uploaded.")).not.toBeInTheDocument();
});
