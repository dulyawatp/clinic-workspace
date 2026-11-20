import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";

const METHOD = "/api/method/clinic_ui.api.encounters.";
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
const draft = { context: { ...context, status: "Closed" }, encounter, notes };
const completed = { ...draft, encounter: { ...encounter, docstatus: 1 } };

function setup(path: string, role: "REC" | "PHA" = "REC") {
  return renderWithDataRouter(appRoutes, { initialEntries: [path], role, sessionMode: "live" });
}

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("REC", { "read.patient": "enabled", "read.appointment": "read_only" })),
    http.get("/api/method/clinic_ui.api.appointments.view", () => reply({
      workspace_patient_id: "WS-PAT", appointment_id: REF, appointment_date: "2026-11-20",
      appointment_time: "09:30:00", status: "Open", modified: VERSION,
      can_reschedule: false, can_checkin: false,
    })),
  );
});
afterEach(() => {
  vi.unstubAllEnvs();
  localStorage.removeItem(LOCALE_KEY);
});

it("starts from appointment detail, saves, reopens the same draft, and completes it once", async () => {
  let current: unknown = { context, encounter: null, notes: { ...notes, items: [] } };
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
  await user.click(screen.getByRole("button", { name: "Add clinical note" }));
  await user.type(screen.getByRole("textbox", { name: "Visit narrative" }), note.text);
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
    expect(await screen.findByText("The encounter is still a draft. Review it before submitting again.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Submit encounter" })).toBeEnabled();
  } else {
    expect(await screen.findByRole("button", { name: "Check encounter status" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Submit encounter" })).not.toBeInTheDocument();
  }
  expect(get).toHaveBeenCalledTimes(2);
  expect(post).toHaveBeenCalledTimes(1);
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
  );
  const posts = postSpy();
  try {
    const page = setup(ROUTES.patient(PAT));
    const user = userEvent.setup();
    for (const entry of [ROUTES.patient(PAT), canonical]) {
      await act(async () => page.router.navigate(entry));
      await user.click(await screen.findByRole("link", { name: "Patient History" }));
      expect(await screen.findByRole("heading", { name: `Visit ${ENC}` })).toBeVisible();
      expect(screen.getByRole("heading", { name: "Visit ENC-OLDER" })).toBeVisible();
      expect(screen.queryByRole("link", { name: `Visit ${ENC}` })).not.toBeInTheDocument();
    }
    expect(listed.mock.calls).toEqual([[PAT], [PAT]]);
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
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
