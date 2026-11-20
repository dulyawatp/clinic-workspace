import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";
import type { LinkedMedication } from "../../transport/native/linked-encounter";

const METHOD = "/api/method/clinic_ui.api.encounters.";
const NOTES_LIST = "/api/method/frappe.client.get_list";
const NOTE_READ = "/api/method/frappe.client.get";
const REF = "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_abcdefghijklmnop";
const APT = "APT-REAL";
const PAT = "PAT-REAL";
const ENC = "ENC-REAL";
const VERSION = "2026-11-20 09:00:00.000001";
const canonical = `${ROUTES.visit(ENC)}?appointment_ref=${REF}`;
const reply = (message: unknown) => HttpResponse.json({ message });
const context = { appointment: APT, patient: PAT, practitioner: "PRAC-REAL", status: "Open" };
const encounter = { id: ENC, modified: VERSION, docstatus: 0, symptoms: ["Pain"], diagnoses: ["D-1"] };
const note = { id: "NOTE-1", modified: VERSION, text: "Follow up next month." };
const notes = { state: "available", items: [note], next_offset: null };
const emptyMedications = { state: "available" as const, complete: true, items: [] };
const draft = { context: { ...context, status: "Closed" }, encounter, notes, medications: emptyMedications };
const completed = { ...draft, encounter: { ...encounter, docstatus: 1 } };

function setup(path: string, role: "REC" | "PHA" = "REC") {
  return renderWithDataRouter(appRoutes, { initialEntries: [path], role, sessionMode: "live" });
}

const prescription: LinkedMedication = {
  id: "RX-ROW", drug_code: "ITEM-1", dosage: "Daily", period: "5 Days", dosage_form: "Capsule",
  medication_request: null, supported: true,
  details: { comment: null, interval: 0, interval_uom: null, strength: 0, strength_uom: null,
    dosage_by_interval: 0, number_of_repeats_allowed: 0, intent: null, priority: null, update_schedule: 1 },
};
const withPrescriptions = (...items: LinkedMedication[]) => ({
  ...draft, medications: { ...emptyMedications, items },
});

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  sessionStorage.clear();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("REC", { "read.patient": "enabled", "read.appointment": "read_only" })),
    http.get("/api/method/clinic_ui.api.appointments.view", () => reply({
      workspace_patient_id: "WS-PAT", appointment_id: REF, appointment_date: "2026-11-20",
      appointment_time: "09:30:00", status: "Open", modified: VERSION,
      can_reschedule: false, can_checkin: false, is_fixture: false,
    })),
  );
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  localStorage.removeItem(LOCALE_KEY);
  sessionStorage.clear();
});

it("selects native prescription masters, adds to the draft, and submits once with native request readback", async () => {
  const saved = vi.fn();
  const submitted = vi.fn();
  let record = withPrescriptions();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(saved.mock.calls.length ? record : {
      context, encounter: null, notes: { ...notes, items: [] }, medications: emptyMedications,
    })),
    http.get("/api/resource/Item", ({ request }) => {
      const params = new URL(request.url).searchParams;
      expect(JSON.parse(params.get("filters") ?? "")).toEqual([["disabled", "=", 0], ["is_stock_item", "=", 1]]);
      expect(JSON.parse(params.get("or_filters") ?? "")).toEqual([["name", "like", "%Brand%"], ["item_name", "like", "%Brand%"]]);
      expect(params.get("limit_page_length")).toBe("20");
      return HttpResponse.json({ data: [{ name: "ITEM-1", item_name: "Example Brand" }] });
    }),
    http.get("/api/resource/Prescription%20Dosage", () => HttpResponse.json({ data: [{ name: "Daily" }] })),
    http.get("/api/resource/Prescription%20Duration", () => HttpResponse.json({ data: [{ name: "5 Days" }] })),
    http.get("/api/resource/Dosage%20Form", () => HttpResponse.json({ data: [{ name: "Capsule" }] })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      saved(await request.json());
      record = { ...withPrescriptions(prescription), encounter: { ...encounter, modified: "NEW-VERSION" } };
      return reply(record);
    }),
    http.post(`${METHOD}complete_linked`, async ({ request }) => {
      submitted(await request.json());
      record = {
        ...withPrescriptions({ ...prescription, supported: false, medication_request: "MR-NATIVE-1" }),
        encounter: { ...encounter, modified: "SUBMITTED-VERSION", docstatus: 1 },
      };
      return reply(record);
    }),
  );
  const page = setup(ROUTES.appointment(REF));
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: "Open encounter" }));
  expect(await screen.findByText("No prescriptions recorded.")).toBeVisible();
  await user.click(await screen.findByRole("button", { name: "Add prescription" }));
  expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  for (const [label, query, result] of [
    ["Stock item", "Brand", "Example Brand · ITEM-1"], ["Dosage", "Dai", "Daily"],
    ["Duration", "Days", "5 Days"], ["Dosage form", "Cap", "Capsule"],
  ] as const) {
    await user.type(screen.getByRole("searchbox", { name: label }), query);
    await user.click(await screen.findByRole("button", { name: result }));
  }
  expect(saved).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("No linked medication request.")).toBeVisible();
  expect(page.router.state.location.pathname + page.router.state.location.search).toBe(canonical);
  expect(saved.mock.calls).toEqual([[{
    appointment_ref: REF, expected_version: null, clinical: {
      medications: { add: [{ drug_code: "ITEM-1", dosage: "Daily", period: "5 Days", dosage_form: "Capsule" }], edit: [] },
    },
  }]]);
  expect(screen.queryByText("MR-NATIVE-1")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Submit encounter" }));
  expect(screen.getByText("Marley can issue prescription orders when you submit this encounter.")).toBeVisible();
  const confirm = screen.getByRole("button", { name: "Confirm submission" });
  act(() => { fireEvent.click(confirm); fireEvent.click(confirm); });
  expect(await screen.findByText("MR-NATIVE-1")).toBeVisible();
  expect(screen.getByText("Issued")).toBeVisible();
  expect(submitted.mock.calls).toEqual([[{ appointment_ref: REF, encounter_id: ENC, expected_version: "NEW-VERSION" }]]);
  page.unmount();
  setup(canonical);
  expect(await screen.findByText("MR-NATIVE-1")).toBeVisible();
  expect(screen.queryByRole("button", { name: /Add prescription|Edit dosage/ })).not.toBeInTheDocument();
  expect(submitted).toHaveBeenCalledTimes(1);
});

it("edits only the exact prescription child dosage links and preserves the Item and other rows", async () => {
  const nonstandard: LinkedMedication = { ...prescription, id: "RX-OTHER", drug_code: "ITEM-OTHER", supported: false,
    details: { ...prescription.details, comment: "Native as-needed instruction", interval: 8, interval_uom: "Hour", dosage_by_interval: 1 } };
  const saved = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(withPrescriptions(nonstandard, prescription))),
    http.get("/api/resource/Prescription%20Dosage", () => HttpResponse.json({ data: [{ name: "Twice daily" }] })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      saved(await request.json());
      return reply(withPrescriptions(nonstandard, { ...prescription, dosage: "Twice daily" }));
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  expect(await screen.findByText("Native as-needed instruction")).toBeVisible();
  expect(screen.getByText("8 Hour")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Edit dosage for RX-OTHER" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit dosage for RX-ROW" }));
  expect(screen.queryByRole("searchbox", { name: "Stock item" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  await user.type(screen.getByRole("searchbox", { name: "Dosage" }), "Twice");
  await user.click(await screen.findByRole("button", { name: "Twice daily" }));
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("Draft saved.")).toBeVisible();
  expect(saved.mock.calls).toEqual([[{
    appointment_ref: REF, expected_version: VERSION, clinical: {
      medications: { add: [], edit: [{ id: "RX-ROW", dosage: "Twice daily", period: "5 Days", dosage_form: "Capsule" }] },
    },
  }]]);
  expect(screen.getByText("ITEM-1")).toBeVisible();
  expect(screen.getByText("ITEM-OTHER")).toBeVisible();
});

it.each(["unavailable", "truncated"] as const)("does not present %s prescription data as an empty list", async (state) => {
  const medications = state === "unavailable" ? { state, complete: false, items: [] } :
    { state: "available", complete: false, items: [prescription] };
  server.use(http.get(`${METHOD}open_for_appointment`, () => reply({ ...draft, medications })));
  const posts = postSpy();
  try {
    setup(canonical);
    expect(await screen.findByText(state === "unavailable" ? "Prescription records are unavailable here." :
      "Only the first 100 prescription rows are shown.")).toBeVisible();
    expect(screen.queryByText("No prescriptions recorded.")).not.toBeInTheDocument();
    if (state === "unavailable") expect(screen.queryByRole("button", { name: "Add prescription" })).not.toBeInTheDocument();
    else expect(screen.getByText("ITEM-1")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Submit encounter" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm submission" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open encounter in Marley" })).toHaveAttribute("href", `/desk/patient-encounter/${ENC}`);
    expect(screen.getByText("Review all prescriptions and complete this encounter in Marley.")).toBeVisible();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("blocks prescription save when replacement text has not been selected and restores the baseline on cancel", async () => {
  const posts = postSpy();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(withPrescriptions(prescription))),
    http.get("/api/resource/Prescription%20Duration", () => HttpResponse.json({ data: [{ name: "7 Days" }] })),
    http.get("/api/resource/Prescription%20Dosage", () => HttpResponse.json({ data: [{ name: "Twice daily" }] })),
  );
  try {
    setup(canonical);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit dosage for RX-ROW" }));
    await user.type(screen.getByRole("searchbox", { name: "Duration" }), "7 Days");
    await user.click(await screen.findByRole("button", { name: "7 Days" }));
    expect(screen.getByRole("button", { name: "Save draft" })).toBeEnabled();
    await user.type(screen.getByRole("searchbox", { name: "Dosage" }), "Twice");
    expect(await screen.findByRole("button", { name: "Twice daily" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel prescription changes" }));
    await user.click(screen.getByRole("button", { name: "Edit dosage for RX-ROW" }));
    expect(screen.getAllByText("Daily")).toHaveLength(2);
    expect(screen.getAllByText("5 Days")).toHaveLength(2);
    expect(screen.queryByText("7 Days")).not.toBeInTheDocument();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("routes the native prescription review link to the encoded authoritative encounter", async () => {
  const id = "ENC?native#exact";
  server.use(http.get(`${METHOD}open_for_appointment`, () => reply({
    ...draft, encounter: { ...encounter, id },
    medications: { state: "unavailable", complete: false, items: [] },
  })));
  setup(ROUTES.linkedVisit(REF, id));
  expect(await screen.findByRole("link", { name: "Open encounter in Marley" }))
    .toHaveAttribute("href", "/desk/patient-encounter/ENC%3Fnative%23exact");
});

it("shows denied prescription lookup honestly without a fallback catalog or free-text selection", async () => {
  const posts = postSpy();
  const item = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(withPrescriptions())),
    http.get("/api/resource/Item", () => {
      item();
      return new HttpResponse(null, { status: 403 });
    }),
  );
  try {
    setup(canonical);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add prescription" }));
    await user.type(screen.getByRole("searchbox", { name: "Stock item" }), "Hidden");
    expect(await screen.findByText("Marley did not allow this lookup.")).toBeVisible();
    expect(item).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Hidden/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("keeps prescription input recoverable after a lost save, even if readback matches", async () => {
  const saved = vi.fn();
  const opened = vi.fn();
  let record = withPrescriptions(prescription);
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => { opened(); return reply(record); }),
    http.get("/api/resource/Prescription%20Duration", () => HttpResponse.json({ data: [{ name: "7 Days" }] })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      saved(await request.json());
      record = { ...withPrescriptions({ ...prescription, period: "7 Days" }), encounter: { ...encounter, modified: "NEW-VERSION" } };
      return HttpResponse.error();
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Edit dosage for RX-ROW" }));
  await user.type(screen.getByRole("searchbox", { name: "Duration" }), "7 Days");
  await user.click(await screen.findByRole("button", { name: "7 Days" }));
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("Your prescription changes")).toBeVisible();
  expect(screen.getByText("RX-ROW")).toBeVisible();
  expect(screen.getByText("7 Days")).toBeVisible();
  await user.click(await screen.findByRole("button", { name: "Check encounter status" }));
  await waitFor(() => expect(opened).toHaveBeenCalledTimes(3));
  expect(screen.queryByText("Draft saved.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
  expect(saved).toHaveBeenCalledTimes(1);
});

it.each([
  { state: "unavailable", complete: true, items: [] },
  { state: "available", complete: true, items: [prescription, prescription] },
])("rejects contradictory prescription projection %# instead of exposing actions", async (medications) => {
  server.use(http.get(`${METHOD}open_for_appointment`, () => reply({ ...draft, medications })));
  setup(canonical);
  expect(await screen.findByText("Encounter could not be loaded.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Add prescription" })).not.toBeInTheDocument();
});

it.each([true, false, undefined] as const)("shows the launch according to the authoritative is_fixture=%s flag", async (is_fixture) => {
  server.use(http.get("/api/method/clinic_ui.api.appointments.view", () => reply({
    workspace_patient_id: "WS-PAT", appointment_id: REF, appointment_date: "2026-11-20",
    appointment_time: "09:30:00", status: "Open", modified: VERSION,
    can_reschedule: false, can_checkin: false, is_fixture,
  })));
  setup(ROUTES.appointment(REF));
  expect(await screen.findByTestId("appointment-reference")).toBeVisible();
  if (is_fixture !== false) expect(screen.queryByRole("link", { name: "Open encounter" })).not.toBeInTheDocument();
  else expect(screen.getByRole("link", { name: "Open encounter" })).toBeVisible();
});

it("starts from appointment detail, saves, reopens the same draft, and completes it once", async () => {
  let current: unknown = { context, encounter: null, notes: { ...notes, items: [] }, medications: emptyMedications };
  const opened = vi.fn();
  const saved = vi.fn();
  const submitted = vi.fn();
  let finish!: () => void;
  const blocked = new Promise<void>((resolve) => { finish = resolve; });
  server.use(
    http.get(`${METHOD}open_for_appointment`, ({ request }) => {
      opened(new URL(request.url).searchParams.get("appointment_ref"));
      return reply(current);
    }),
    http.get("/api/resource/Complaint", () => HttpResponse.json({ data: [{ name: "Pain" }] })),
    http.get("/api/resource/Diagnosis", () => HttpResponse.json({ data: [{ name: "D-1" }] })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      saved(await request.json(), request.headers.get("X-Frappe-CSRF-Token"));
      current = draft;
      return reply(current);
    }),
    http.post(`${METHOD}complete_linked`, async ({ request }) => {
      submitted(await request.json());
      await blocked;
      current = completed;
      return reply(current);
    }),
  );
  const user = userEvent.setup();
  const page = setup(ROUTES.appointment(REF));
  const launch = await screen.findByRole("link", { name: "Open encounter" });
  expect(launch.getAttribute("href")).not.toContain(`/visits/${REF}`);
  await user.click(launch);
  expect(await screen.findByText(APT)).toBeVisible();
  expect(opened).toHaveBeenCalledWith(REF);
  expect(saved).not.toHaveBeenCalled();
  await user.type(screen.getByRole("searchbox", { name: "Symptoms" }), "Pain");
  await user.click(await screen.findByRole("button", { name: "Pain" }));
  await user.type(screen.getByRole("searchbox", { name: "Diagnoses" }), "D-1");
  await user.click(await screen.findByRole("button", { name: "D-1" }));
  const composer = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(composer).toHaveAccessibleDescription(/History.*Examination.*Plan.*Follow-up/);
  expect(screen.getByText("More details").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByRole("textbox", { name: /encounter comment/i })).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Symptoms" }).closest(".visit-preview-fields"))
    .toBe(screen.getByRole("searchbox", { name: "Diagnoses" }).closest(".visit-preview-fields"));
  await user.type(composer, note.text);
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText(note.text)).toBeVisible();
  expect(saved).toHaveBeenCalledWith({
    appointment_ref: REF, expected_version: null,
    clinical: { symptoms: ["Pain"], diagnoses: ["D-1"], note: { id: null, modified: null, text: note.text } },
  }, "native-csrf");
  expect(page.router.state.location.pathname + page.router.state.location.search).toBe(canonical);
  await act(async () => page.router.navigate(ROUTES.appointment(REF)));
  await user.click(await screen.findByRole("link", { name: "Open encounter" }));
  expect(await screen.findByText(note.text)).toBeVisible();
  expect(page.router.state.location.pathname + page.router.state.location.search).toBe(canonical);
  await user.click(screen.getByRole("button", { name: "Submit encounter" }));
  const confirm = screen.getByRole("button", { name: "Confirm submission" });
  act(() => {
    fireEvent.click(confirm);
    fireEvent.click(confirm);
  });
  await waitFor(() => expect(submitted).toHaveBeenCalledTimes(1));
  expect(submitted).toHaveBeenCalledWith({ appointment_ref: REF, encounter_id: ENC, expected_version: VERSION });
  expect(screen.getByRole("button", { name: "Confirm submission" })).toBeDisabled();
  finish();
  expect(await screen.findByText("Submitted")).toBeVisible();
  expect(screen.queryByRole("button", { name: /submit encounter|confirm submission|save draft/i })).not.toBeInTheDocument();
  expect(page.router.state.location.pathname + page.router.state.location.search).toBe(canonical);
  page.unmount();
  setup(canonical);
  expect(await screen.findByText("Submitted")).toBeVisible();
  expect(screen.getByText(note.text)).toBeVisible();
  expect(screen.getByText("Pain")).toBeVisible();
  expect(screen.getByText("D-1")).toBeVisible();
});

it.each(["mismatch", "malformed", "denied", "duplicate ref", "wrong appointment"] as const)("stops before actions for %s context", async (reason) => {
  const get = vi.fn();
  server.use(http.get(`${METHOD}open_for_appointment`, () => {
    get();
    if (reason === "denied") return new HttpResponse(null, { status: 403 });
    if (reason === "malformed") return reply({ encounter });
    if (reason === "wrong appointment") return reply(draft);
    return reply({ ...draft, encounter: { ...encounter, id: "WRONG-ID" } });
  }));
  setup(reason === "duplicate ref" ? `${canonical}&appointment_ref=APT-OTHER` :
    reason === "wrong appointment" ? `${ROUTES.visit(ENC)}?appointment_ref=APT-OTHER` : canonical);
  expect(await screen.findByText(reason === "denied" ? "Permission denied" : "Encounter could not be loaded.")).toBeVisible();
  expect(screen.queryByText("WRONG-ID")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
  if (reason === "duplicate ref") expect(get).not.toHaveBeenCalled();
});

it("treats the bare launch-segment docname as a native encounter read", async () => {
  const read = vi.fn();
  const linked = vi.fn();
  server.use(
    http.get(`${METHOD}view`, ({ request }) => {
      read(new URL(request.url).searchParams.get("encounter_id"));
      return reply({
        id: "appointment", patientId: PAT, patientName: "Saved Patient", practitioner: context.practitioner,
        encounterDate: null, docstatus: 0, symptoms: ["Native symptom"], diagnoses: [],
      });
    }),
    http.get(`${METHOD}open_for_appointment`, () => {
      linked();
      return reply(draft);
    }),
  );
  setup(ROUTES.visit("appointment"));
  expect(await screen.findByRole("heading", { name: "Saved Patient" })).toBeVisible();
  expect(read).toHaveBeenCalledExactlyOnceWith("appointment");
  expect(linked).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
});

it("lets Frappe decide linked encounter access without adding a clinic role gate", async () => {
  const get = vi.fn();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("PHA", {})),
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      return reply(completed);
    }),
  );
  setup(canonical, "PHA");
  expect(await screen.findByText("Submitted")).toBeVisible();
  expect(get).toHaveBeenCalledTimes(1);
  expect(screen.queryByTestId("role-guard-denied")).not.toBeInTheDocument();
});

it.each(["submitted", "draft", "unavailable"] as const)("reconciles a lost submit response with a %s GET", async (result) => {
  const get = vi.fn();
  const post = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      if (!post.mock.calls.length || result === "draft") return reply(draft);
      return result === "submitted" ? reply(completed) : new HttpResponse(null, { status: 503 });
    }),
    http.post(`${METHOD}complete_linked`, () => {
      post();
      return HttpResponse.error();
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Submit encounter" }));
  await user.click(screen.getByRole("button", { name: "Confirm submission" }));
  if (result === "submitted") expect(await screen.findByText("Submitted")).toBeVisible();
  else if (result === "draft") {
    expect(await screen.findByText("The encounter still appears as a draft. Submission is not confirmed.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Submit encounter" })).not.toBeInTheDocument();
  } else {
    expect(await screen.findByRole("button", { name: "Check encounter status" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Submit encounter" })).not.toBeInTheDocument();
  }
  expect(get).toHaveBeenCalledTimes(2);
  expect(post).toHaveBeenCalledTimes(1);
});

it("keeps an ambiguous submit locked through repeated draft reads until a delayed commit appears", async () => {
  let current = draft;
  const get = vi.fn();
  const post = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      return reply(current);
    }),
    http.post(`${METHOD}complete_linked`, () => {
      post();
      return HttpResponse.error();
    }),
  );
  const page = setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Submit encounter" }));
  await user.click(screen.getByRole("button", { name: "Confirm submission" }));
  let check = await screen.findByRole("button", { name: "Check encounter status" });
  await user.click(check);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  check = await screen.findByRole("button", { name: "Check encounter status" });
  expect(screen.queryByRole("button", { name: /submit encounter|confirm submission|save draft/i })).not.toBeInTheDocument();
  page.unmount();
  setup(canonical);
  check = await screen.findByRole("button", { name: "Check encounter status" });
  expect(screen.queryByRole("button", { name: "Submit encounter" })).not.toBeInTheDocument();
  current = completed;
  await user.click(check);
  expect(await screen.findByText("Submitted")).toBeVisible();
  expect(screen.getByText(note.text)).toBeVisible();
  expect(get).toHaveBeenCalledTimes(5);
  expect(post).toHaveBeenCalledTimes(1);
});

it("distinguishes a write explicitly rejected by Frappe from a lost submit response", async () => {
  const get = vi.fn();
  const post = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      return reply(draft);
    }),
    http.post(`${METHOD}complete_linked`, () => {
      post();
      return new HttpResponse(null, { status: 417 });
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Submit encounter" }));
  await user.click(screen.getByRole("button", { name: "Confirm submission" }));
  expect(await screen.findByText("Marley rejected this change. Check the current record before trying again.")).toBeVisible();
  expect(get).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Check encounter status" }));
  expect(await screen.findByRole("button", { name: "Submit encounter" })).toBeEnabled();
  expect(get).toHaveBeenCalledTimes(2);
  expect(post).toHaveBeenCalledTimes(1);
});

it("preserves coded changes and note input even when a delayed read matches the ambiguous save", async () => {
  const changedVersion = "2026-11-20 09:45:00.000001";
  const noteText = "New follow-up instructions.";
  let current = draft;
  const get = vi.fn();
  const save = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      return reply(current);
    }),
    http.get("/api/resource/Complaint", () => HttpResponse.json({ data: [{ name: "Itching" }] })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      save(await request.json());
      return HttpResponse.error();
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  await user.type(await screen.findByRole("searchbox", { name: "Symptoms" }), "Itching");
  await user.click(await screen.findByRole("button", { name: "Itching" }));
  await user.click(screen.getByRole("button", { name: "Edit note NOTE-1" }));
  const editable = screen.getByRole("textbox", { name: "Visit narrative" });
  await user.clear(editable);
  await user.type(editable, noteText);
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("The save is not confirmed. Your changes are shown below so you can copy them if needed.")).toBeVisible();
  let input = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(input).toHaveValue(noteText);
  expect(input).toHaveAttribute("readonly");
  expect(screen.getByText("Itching")).toBeVisible();
  expect(screen.queryByRole("button", { name: /save draft|submit encounter/i })).not.toBeInTheDocument();
  current = {
    ...draft,
    encounter: { ...encounter, modified: changedVersion, symptoms: ["Pain", "Itching"] },
    notes: { ...notes, items: [{ ...note, text: noteText }] }, // right content, unchanged note version
  };
  await user.click(screen.getByRole("button", { name: "Check encounter status" }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  input = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(input).toHaveValue(noteText);
  expect(input).toHaveAttribute("readonly");
  current = { ...current, notes: { ...notes, items: [{ ...note, modified: changedVersion, text: noteText }] } };
  await user.click(await screen.findByRole("button", { name: "Check encounter status" }));
  expect(await screen.findByText("The save is not confirmed. Your changes are shown below so you can copy them if needed.")).toBeVisible();
  input = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(input).toHaveValue(noteText);
  expect(input).toHaveAttribute("readonly");
  expect(screen.getByText("Itching")).toBeVisible();
  expect(screen.queryByText("Draft saved.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save draft|submit encounter/i })).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(4);
  expect(save.mock.calls).toEqual([[{
    appointment_ref: REF,
    expected_version: VERSION,
    clinical: { symptoms: ["Pain", "Itching"], note: { id: note.id, modified: note.modified, text: noteText } },
  }]]);
});

it("keeps draft reads and Patient History available when recovery marker reads are blocked on remount", async () => {
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(draft)),
    http.get(`${METHOD}list_for_patient`, () => reply({
      items: [{ id: ENC, patientId: PAT, patientName: "Timeline Patient", practitioner: context.practitioner, encounterDate: null, docstatus: 0 }],
      nextOffset: null,
    })),
  );
  const page = setup(canonical);
  expect(await screen.findByRole("button", { name: "Submit encounter" })).toBeEnabled();
  page.unmount();
  const read = Storage.prototype.getItem;
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key) {
    if (key.startsWith("clinic.linked.")) throw new DOMException("Blocked", "SecurityError");
    return read.call(this, key);
  });
  const posts = postSpy();
  try {
    setup(canonical);
    expect(await screen.findByText("This browser could not protect against a repeat request. No changes were sent.")).toBeVisible();
    expect(screen.getByText(note.text)).toBeVisible();
    const history = screen.getByRole("link", { name: "Patient History" });
    expect(history).toHaveAttribute("href", ROUTES.patientHistory(PAT));
    expect(screen.queryByRole("button", { name: /save draft|submit encounter|add clinical note/i })).not.toBeInTheDocument();
    await userEvent.setup().click(history);
    expect(await screen.findByRole("heading", { name: `Visit ${ENC}` })).toBeVisible();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("sends no POST if browser storage silently drops the recovery marker", async () => {
  server.use(http.get(`${METHOD}open_for_appointment`, () => reply(draft)));
  const write = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (!key.startsWith("clinic.linked.")) write.call(this, key, value);
  });
  const posts = postSpy();
  try {
    setup(canonical);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Submit encounter" }));
    await user.click(screen.getByRole("button", { name: "Confirm submission" }));
    expect(await screen.findByText("This browser could not protect against a repeat request. No changes were sent.")).toBeVisible();
    expect(screen.getByText(note.text)).toBeVisible();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("does not treat an indistinguishable new note as proof of an ambiguous save", async () => {
  let current = draft;
  const get = vi.fn();
  const save = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => {
      get();
      return reply(current);
    }),
    http.post(`${METHOD}save_linked`, () => {
      save();
      return HttpResponse.error();
    }),
  );
  const page = setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Add clinical note" }));
  await user.type(screen.getByRole("textbox", { name: "Visit narrative" }), "Possible duplicate note.");
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByRole("button", { name: "Check encounter status" });
  current = {
    ...draft,
    encounter: { ...encounter, modified: "2026-11-20 09:45:00.000001" },
    notes: { ...notes, items: [...notes.items, { ...note, id: "NOTE-2", text: "Possible duplicate note." }] },
  };
  await user.click(screen.getByRole("button", { name: "Check encounter status" }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
  expect(await screen.findByRole("button", { name: "Check encounter status" })).toBeEnabled();
  expect(screen.getByRole("textbox", { name: "Visit narrative" })).toHaveValue("Possible duplicate note.");
  expect(JSON.stringify(sessionStorage)).not.toMatch(/Possible duplicate note|PAT-REAL|ENC-REAL|APT-REAL/);
  expect(screen.queryByText("Draft saved.")).not.toBeInTheDocument();
  page.unmount();
  setup(canonical);
  expect(await screen.findByText("The save is not confirmed. Verify the encounter in Marley before making further changes.")).toBeVisible();
  expect(get).toHaveBeenCalledTimes(4);
  expect(screen.queryByRole("button", { name: /save draft|submit encounter/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Visit narrative" })).not.toBeInTheDocument();
  expect(save).toHaveBeenCalledTimes(1);
});

it("paginates clinical notes and updates only the explicitly selected note and version", async () => {
  const rich = {
    id: "NOTE-RICH",
    modified: VERSION,
    text: '<p>Native <strong>formatted</strong> note.</p><img src="/not-a-note-image"><script>DO NOT RENDER</script>',
  };
  const older = { id: "NOTE-2", modified: "2026-11-19 09:00:00.000001", text: "Second note." };
  const offsets = vi.fn();
  const saved = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, ({ request }) => {
      const offset = new URL(request.url).searchParams.get("note_offset");
      offsets(offset);
      return reply({ ...draft, notes: offset === "0"
        ? { ...notes, items: [note, rich], next_offset: 20 }
        : { ...notes, items: [older] } });
    }),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      saved(await request.json());
      return reply({
        ...draft,
        notes: { ...notes, items: [note, rich, { ...older, text: "Updated note &lt; 3." }] },
      });
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  expect(await screen.findByText("Native formatted note.")).toBeVisible();
  expect(screen.queryByText("DO NOT RENDER")).not.toBeInTheDocument();
  expect(document.querySelector('img[src="/not-a-note-image"]')).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit note NOTE-RICH" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Load more notes" }));
  expect(await screen.findByText(older.text)).toBeVisible();
  expect(screen.getByText(note.text)).toBeVisible();
  expect(offsets.mock.calls).toEqual([["0"], ["20"]]);
  await user.click(screen.getByRole("button", { name: "Edit note NOTE-2" }));
  const input = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(input).toHaveValue(older.text);
  await user.clear(input);
  await user.type(input, "Updated note < 3.");
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("Updated note < 3.")).toBeVisible();
  expect(saved.mock.calls).toEqual([[{
    appointment_ref: REF,
    expected_version: VERSION,
    clinical: { note: { id: older.id, modified: older.modified, text: "Updated note &lt; 3." } },
  }]]);
});

const formattedNote = {
  ...note,
  text: '<div class="ql-editor read-mode"><h2>Plan</h2><p><strong>Return</strong> &nbsp;<em>soon</em>.</p><ul><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span><u>Review</u></li></ul></div>',
};

it.each([200, 403, 417])("edits a formatted note with its version and preserves unrelated marks (HTTP %s)", async (status) => {
  const save = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply({ ...draft, notes: { ...notes, items: [formattedNote] } })),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      const body = await request.json() as { clinical: { note: typeof formattedNote } };
      save(body);
      return status === 200
        ? reply({ ...draft, notes: { ...notes, items: [{ ...formattedNote, text: body.clinical.note.text }] } })
        : new HttpResponse(null, { status });
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  expect(await screen.findByText("Return", { selector: "strong" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Edit note NOTE-1" }));
  const editor = screen.getByRole("textbox", { name: "Visit narrative" });
  expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  expect(save).not.toHaveBeenCalled();
  await act(async () => {
    const editableMark = editor.querySelector("strong")?.firstChild;
    if (!editableMark) throw new Error("Formatted editor lost the bold run");
    editableMark.textContent = "Return next week";
    fireEvent.input(editor);
  });
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith({
    appointment_ref: REF, expected_version: VERSION, clinical: { note: {
      id: note.id, modified: note.modified,
      text: '<div class="ql-editor read-mode"><h2>Plan</h2><p><strong>Return next week</strong> &nbsp;<em>soon</em>.</p><ul><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span><u>Review</u></li></ul></div>',
    } },
  });
  if (status === 200) {
    expect(await screen.findByText("Draft saved.")).toBeVisible();
    expect(screen.getByText("soon", { selector: "em" })).toBeVisible();
  } else {
    expect(await screen.findByText(status === 403 ? "Marley did not allow this action." :
      "Marley rejected this change. Check the current record before trying again.")).toBeVisible();
    expect(editor).toHaveTextContent("Return next week");
  }
});

it.each(["unchanged", "unsupported"] as const)("does not send a note update for %s formatted content", async (kind) => {
  const original = kind === "unchanged" ? formattedNote : {
    ...formattedNote,
    text: '<div class="ql-editor read-mode"><p><strong>Visible</strong><img src="/no-note-image" onerror="alert(1)"><a href="javascript:alert(1)">link</a><script>HIDDEN</script></p></div>',
  };
  const projected = { ...draft, notes: { ...notes, items: [original] } };
  const save = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(projected)),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      save(await request.json());
      return reply(projected);
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  const remove = await screen.findByRole("button", { name: "Remove Pain" });
  if (kind === "unchanged") {
    await user.click(screen.getByRole("button", { name: "Edit note NOTE-1" }));
    expect(screen.getByRole("textbox", { name: "Visit narrative" })).toHaveTextContent("Review");
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  } else {
    expect(screen.getByText("Formatted note shown as text. Edit it in Marley to preserve formatting.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit note NOTE-1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "link" })).not.toBeInTheDocument();
    expect(screen.queryByText("HIDDEN")).not.toBeInTheDocument();
    expect(document.querySelector('img[src="/no-note-image"]')).toBeNull();
  }
  await user.click(remove);
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith({ appointment_ref: REF, expected_version: VERSION, clinical: { symptoms: [] } });
});

it("inserts only plain clipboard text and refuses dropped or pasted files in the formatted editor", async () => {
  const save = vi.fn();
  const projected = { ...draft, notes: { ...notes, items: [formattedNote] } };
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply(projected)),
    http.post(`${METHOD}save_linked`, async ({ request }) => {
      save(await request.json());
      return reply(projected);
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Edit note NOTE-1" }));
  const editor = screen.getByRole("textbox", { name: "Visit narrative" });
  const paragraph = editor.querySelector("p")?.lastChild;
  if (!paragraph) throw new Error("Missing paragraph");
  editor.focus();
  const range = document.createRange();
  range.setStart(paragraph, paragraph.textContent?.length ?? 0);
  range.collapse(true);
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(range);
  const read = vi.fn((type: string) => type === "text/plain" ? " <pasted>" : '<a href="javascript:alert(1)">HTML Only</a>');
  await act(async () => {
    fireEvent.paste(editor, { clipboardData: { files: [], getData: read } });
  });
  expect(read.mock.calls).toEqual([["text/plain"]]);
  expect(editor).toHaveTextContent("<pasted>");
  expect(editor.querySelector("a")).toBeNull();
  const write = vi.fn();
  fireEvent.copy(editor, { clipboardData: { setData: write } });
  expect(write.mock.calls.map(([mime]) => mime)).toEqual(["text/plain"]);
  const file = new File(["data"], "not-a-note.png", { type: "image/png" });
  await act(async () => {
    fireEvent.paste(editor, { clipboardData: { files: [file], getData: read } });
    fireEvent.drop(editor, { dataTransfer: { files: [file], getData: read } });
  });
  expect(read).toHaveBeenCalledTimes(1);
  expect(editor.querySelector("img")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0]?.[0]?.clinical?.note?.text).toContain(" &lt;pasted&gt;");
});

it("distinguishes unavailable notes from an empty list and still submits the authorized encounter", async () => {
  const unavailable = { state: "unavailable", items: [], next_offset: null };
  const post = vi.fn();
  server.use(
    http.get(`${METHOD}open_for_appointment`, () => reply({ ...draft, notes: unavailable })),
    http.post(`${METHOD}complete_linked`, () => {
      post();
      return reply({ ...completed, notes: unavailable });
    }),
  );
  setup(canonical);
  const user = userEvent.setup();
  expect(await screen.findByText("Clinical notes are not available here.")).toBeVisible();
  expect(screen.queryByText("No clinical notes to display.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add clinical note" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Submit encounter" }));
  await user.click(screen.getByRole("button", { name: "Confirm submission" }));
  expect(await screen.findByText("Submitted")).toBeVisible();
  expect(post).toHaveBeenCalledTimes(1);
});

it("opens the same read-only patient timeline from Patient History and the encounter", async () => {
  const listed = vi.fn();
  const row = { id: ENC, patientId: PAT, patientName: "Native patient", practitioner: "PRAC-REAL", encounterDate: "2026-11-20", docstatus: 1 };
  server.use(
    http.get("/api/method/clinic_ui.api.patients.view", () => reply({
      workspace_patient_id: PAT, display_name: row.patientName, first_name: null, last_name: null, sex: null, dob: null, status: null,
    })),
    http.get(`${METHOD}list_for_patient`, ({ request }) => {
      listed(new URL(request.url).searchParams.get("patient_ref"));
      return reply({ items: [row, { ...row, id: "ENC-OLDER" }], nextOffset: null });
    }),
    http.get(`${METHOD}open_for_appointment`, () => reply(completed)),
    http.get(`${METHOD}view`, () => reply({ ...row, symptoms: [], diagnoses: [] })),
    http.post(NOTES_LIST, () => new HttpResponse(null, { status: 503 })),
  );
  const posts = postSpy();
  try {
    const page = setup(ROUTES.patient(PAT));
    const user = userEvent.setup();
    for (const entry of [ROUTES.patient(PAT), canonical]) {
      await act(async () => page.router.navigate(entry));
      if (entry === canonical) {
        expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
        expect(screen.getByText("20/11/2026")).toBeVisible();
        expect(screen.queryByRole("textbox", { name: "Visit narrative" })).not.toBeInTheDocument();
      }
      await user.click(await screen.findByRole("link", { name: "Patient History" }));
      expect(await screen.findByRole("heading", { name: `Visit ${ENC}` })).toBeVisible();
      expect(screen.getByRole("heading", { name: row.patientName })).toBeVisible();
      expect(screen.getByRole("heading", { name: "Visit ENC-OLDER" })).toBeVisible();
      expect(screen.queryByRole("link", { name: `Visit ${ENC}` })).not.toBeInTheDocument();
      const card = screen.getByRole("article", { name: `Visit ${ENC}` });
      const [lab, photo, narrative] = Array.from(card.querySelectorAll(
        ".visit-history-report, .visit-history-evidence, .visit-history-narrative",
      ));
      expect(lab).toHaveTextContent("Lab reports");
      expect(photo).toHaveTextContent("Same-visit photo status is not available here.");
      expect(narrative).toHaveTextContent("Read visit note");
      const detail = narrative?.querySelector("details");
      expect(detail).not.toHaveAttribute("open");
      await user.click(within(card).getByText("Read visit note"));
      expect(detail).toHaveAttribute("open");
      expect(await within(card).findByText("Clinical notes are not available here.")).toBeVisible();
      expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
    }
    expect(listed.mock.calls).toEqual([[PAT], [PAT]]);
    expect(posts.posts.map((url) => new URL(url).pathname)).toEqual([NOTES_LIST, NOTES_LIST]);
  } finally {
    posts.stop();
  }
});

const historyRow = { id: ENC, patientId: PAT, patientName: "Native patient", practitioner: null, encounterDate: null, docstatus: 1 };
const historyNote = (name: string, text: string) => ({
  doctype: "Clinical Note", name, patient: PAT, reference_doc: "Patient Encounter", reference_name: ENC, note: text,
});

async function historyRpcBody(request: Request, path: string) {
  const url = new URL(request.url);
  expect(url.href).toBe(new URL(path, window.location.origin).href);
  expect(request.headers.get("Content-Type")).toBe("application/json");
  expect(request.headers.get("X-Frappe-CSRF-Token")).toBe("native-csrf");
  return request.json();
}

it("reads exact native names lazily, rechecks each note, renders safely and paginates without editor controls", async () => {
  const lists = vi.fn();
  const reads = vi.fn();
  const names = Array.from({ length: 11 }, (_, index) => `${PAT}-${ENC}-${index.toString().padStart(5, "0")}`);
  server.use(
    http.get(`${METHOD}list_for_patient`, () => reply({ items: [historyRow], nextOffset: null })),
    http.post(NOTES_LIST, async ({ request }) => {
      const body = await historyRpcBody(request, NOTES_LIST);
      lists(body);
      return reply((body.limit_start === 0 ? names : names.slice(10)).map((name) => ({ name })));
    }),
    http.post(NOTE_READ, async ({ request }) => {
      const body = await historyRpcBody(request, NOTE_READ);
      reads(body);
      const { name } = body;
      const text = name === names[0] ? '<div class="ql-editor read-mode"><p><strong>Formatted follow-up</strong></p></div>' :
        name === names[1] ? '<p>Fallback note</p><img src="/unexpected-file"><script>unsafe payload</script>' :
          `Narrative ${name}`;
      return reply(historyNote(name, text));
    }),
  );
  const posts = postSpy();
  try {
    // Route is a workspace alias, not the native patient identity used in the filter.
    setup(ROUTES.patientHistory("workspace-alias"));
    const summary = await screen.findByText("Read visit note");
    expect(lists).not.toHaveBeenCalled();
    expect(reads).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(summary);
    const supported = await screen.findByText("Formatted follow-up", { selector: "strong" });
    expect(supported).toBeVisible();
    expect(screen.getByText("Fallback note")).toBeVisible();
    expect(screen.queryByText("unsafe payload")).not.toBeInTheDocument();
    expect(document.querySelector('img[src="/unexpected-file"]')).toBeNull();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(reads.mock.calls).toEqual(names.slice(0, 10).map((name) => [{ doctype: "Clinical Note", name }]));
    await user.click(screen.getByRole("button", { name: "Load more notes" }));
    expect(await screen.findByText(`Narrative ${names[10]}`)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more notes" })).not.toBeInTheDocument();
    expect(lists.mock.calls).toEqual([0, 10].map((offset) => [{
      doctype: "Clinical Note", filters: { reference_doc: "Patient Encounter", reference_name: ENC, patient: PAT },
      fields: ["name"], order_by: "name asc", limit_page_length: 11, limit_start: offset,
    }]));
    expect(reads).toHaveBeenLastCalledWith({ doctype: "Clinical Note", name: names[10] });
    expect(posts.posts.map((url) => new URL(url).pathname)).toEqual([
      NOTES_LIST, ...Array<string>(10).fill(NOTE_READ), NOTES_LIST, NOTE_READ,
    ]);
  } finally {
    posts.stop();
  }
});

it.each([
  ["denied list", 403, null, "Permission denied"],
  ["denied document", 200, 403, "Permission denied"],
  ["empty", 200, null, "No clinical notes to display."],
  ["mismatched patient", 200, { patient: "OTHER" }, "Clinical notes are not available here."],
  ["mismatched encounter", 200, { reference_name: "OTHER" }, "Clinical notes are not available here."],
  ["wrong reference doctype", 200, { reference_doc: "Patient Appointment" }, "Clinical notes are not available here."],
  ["masked note", 200, { note: "XXXXXXXX" }, "Clinical notes are not available here."],
  ["stripped note", 200, { note: undefined }, "Clinical notes are not available here."],
  ["unavailable list", 503, null, "Clinical notes are not available here."],
] as const)("shows the correct timeline note state: %s", async (_, status, document, message) => {
  const read = vi.fn();
  server.use(
    http.get(`${METHOD}list_for_patient`, () => reply({ items: [historyRow], nextOffset: null })),
    http.post(NOTES_LIST, () => status !== 200 ? new HttpResponse(null, { status }) :
      reply(document === null ? [] : [{ name: "NOTE-PRIVATE" }])),
    http.post(NOTE_READ, () => {
      read();
      return typeof document === "number" ? new HttpResponse(null, { status: document }) :
        reply({ ...historyNote("NOTE-PRIVATE", "Private note must not render"), ...document });
    }),
  );
  setup(ROUTES.patientHistory(PAT));
  await userEvent.setup().click(await screen.findByText("Read visit note"));
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.queryByText("Private note must not render")).not.toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(document === null ? 0 : 1);
});

it("does not expose the standalone unlinked draft in staff navigation or the router", async () => {
  const target = "/clinic/encounters/new";
  server.use(http.get("/api/method/clinic_ui.api.directory.synthetic_patients", () => reply({ items: [] })));
  const posts = postSpy();
  try {
    const page = setup(ROUTES.appointment(REF));
    expect(await screen.findByTestId("appointment-reference")).toHaveTextContent(REF);
    expect(document.querySelector(`a[href="${target}"]`)).toBeNull();
    await act(async () => page.router.navigate(ROUTES.patients));
    await screen.findByRole("heading", { level: 1 });
    expect(document.querySelector(`a[href="${target}"]`)).toBeNull();
    await act(async () => page.router.navigate(target));
    expect(await screen.findByTestId("route-error")).toBeVisible();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});
