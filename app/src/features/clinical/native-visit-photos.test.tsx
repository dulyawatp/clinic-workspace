import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { nativeSession, NATIVE_BOOTSTRAP } from "../../test/network-spy";
import { renderWithDataRouter, setViewport } from "../../test/render";
import type { VisitAnnotationCallbacks } from "../../transport/native/visit-annotations";
import { LinkedVisitWorkspace } from "./LinkedVisitWorkspace";

const rpc = "/api/method/";
const method = `${rpc}clinic_photos.api.visit_photos.`;
const ref = "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_abcdefghijklmnop";
const first = {
  id: "ENC-native-one", patientId: "PAT-native", patientName: "Synthetic Patient",
  practitioner: null, encounterDate: "2026-11-20", docstatus: 1,
};
const second = { ...first, id: "ENC-native-two" };
const reply = (message: unknown) => HttpResponse.json({ message });
const metadata = { photo: "INTERNAL-PHOTO", body_area: "Face", viewpoint: "Front" };
const pair = (appointment: string, before: object | null, after: object | null) => ({ appointment, before, after });
const record = (id: string, appointment: string | null, patient = first.patientId) =>
  reply({ doctype: "Patient Encounter", name: id, patient, appointment });
const history = () => ROUTES.patientHistory(first.patientId);
const setup = (path = history()) => renderWithDataRouter(appRoutes, { initialEntries: [path], role: "DOC", sessionMode: "live" });
const visit = async (id = first.id) => within(await screen.findByRole("article", { name: `Visit ${id}` }));
const openPanel = async (scope: ReturnType<typeof within> = within(document.body)) => {
  const title = await scope.findByText("View visit photos", { selector: "summary" });
  await userEvent.setup().click(title);
  return title;
};

async function body(request: Request) {
  expect(request.headers.get("X-Frappe-CSRF-Token")).toBe("native-csrf");
  expect(new URL(request.url).search).toBe("");
  return request.json();
}

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC")),
    http.get(`${rpc}clinic_ui.api.patients.view`, () => reply({
      workspace_patient_id: first.patientId, display_name: first.patientName,
      first_name: null, last_name: null, sex: null, dob: null, status: null,
    })),
    http.get(`${rpc}clinic_ui.api.encounters.list_for_patient`, () => reply({ items: [first, second], nextOffset: null })),
    http.get(`${rpc}clinic_ui.api.encounters.view`, () => reply({ ...first, symptoms: [], diagnoses: [] })),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  localStorage.removeItem(LOCALE_KEY);
});

it("loads only opened History originals, compares both without refetch, and revokes on close", async () => {
  setViewport(390);
  const restore = installDialogMethods();
  let id = 0;
  const create = vi.fn(() => `blob:synthetic-visit-photo-${++id}`);
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = create;
    static override revokeObjectURL = revoke;
  });
  const lookups = vi.fn();
  const pairs = vi.fn();
  const images = vi.fn();
  const annotations = vi.fn();
  server.use(
    http.post(`${rpc}frappe.client.get`, async ({ request }) => {
      const input = await body(request) as { name: string };
      lookups(input);
      return record(input.name, input.name === first.id ? "APT-one" : "APT-two");
    }),
    http.post(`${method}read_pair`, async ({ request }) => {
      const input = await body(request) as { payload: { appointment: string } };
      pairs(input);
      const { appointment } = input.payload;
      return reply({ ...pair(appointment, metadata, appointment === "APT-two" ? metadata : null), drawings_enabled: true });
    }),
    http.post(`${method}read_image`, async ({ request }) => {
      images(await body(request));
      return new HttpResponse(Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0]), {
        headers: { "Content-Type": "image/png" },
      });
    }),
    http.post(`${rpc}clinic_photos.api.visit_annotations.*`, () => {
      annotations();
      return HttpResponse.error();
    }),
  );
  try {
    setup();
    const earlier = await visit();
    const later = await visit(second.id);
    expect(screen.getByRole("article", { name: `Visit ${second.id}` }).querySelector(".visit-history-evidence"))
      .toContainElement(later.getByRole("heading", { name: "Same-visit photos" }));
    expect(lookups).not.toHaveBeenCalled();
    expect(pairs).not.toHaveBeenCalled();
    expect(images).not.toHaveBeenCalled();
    await openPanel(earlier);
    expect(await earlier.findByRole("button", { name: "View Before photo" })).toBeVisible();
    expect(earlier.queryByRole("button", { name: "View After photo" })).toBeNull();
    expect(earlier.getByText("Pair incomplete")).toBeVisible();
    expect(earlier.getByText("Not recorded")).toBeVisible();
    expect(earlier.queryByRole("button", { name: "Compare pair" })).toBeNull();
    expect(later.queryByRole("button", { name: "View Before photo" })).toBeNull();
    expect(images).toHaveBeenCalledExactlyOnceWith({ payload: { appointment: "APT-one", slot: "Before" } });
    const disclosure = await openPanel(later);
    const compare = await later.findByRole("button", { name: "Compare pair" });
    const before = later.getByRole("img", { name: "Before photo" });
    const after = later.getByRole("img", { name: "After photo" });
    expect(before.closest(".visit-photo-shots")).toBe(after.closest(".visit-photo-shots"));
    expect(before.getAttribute("src")).toMatch(/^blob:/);
    expect(after.getAttribute("src")).toMatch(/^blob:/);
    expect(before.getAttribute("src")).not.toEqual(after.getAttribute("src"));
    expect(lookups.mock.calls).toEqual([
      [{ doctype: "Patient Encounter", name: first.id }],
      [{ doctype: "Patient Encounter", name: second.id }],
    ]);
    expect(pairs.mock.calls).toEqual([[{ payload: { appointment: "APT-one" } }], [{ payload: { appointment: "APT-two" } }]]);
    expect(screen.queryByText("INTERNAL-PHOTO")).toBeNull();
    expect(later.queryByRole("button", { name: /annotate|capture|upload|consent|save/i })).toBeNull();
    await userEvent.setup().click(compare);
    const dialog = screen.getByRole("dialog", { name: "Compare before & after" });
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    expect(within(dialog).getByRole("img", { name: "Before photo" })).toHaveAttribute("src", before.getAttribute("src"));
    expect(within(dialog).getByRole("img", { name: "After photo" })).toHaveAttribute("src", after.getAttribute("src"));
    expect(images).toHaveBeenCalledTimes(3);
    expect(images).toHaveBeenCalledWith({ payload: { appointment: "APT-two", slot: "Before" } });
    expect(images).toHaveBeenCalledWith({ payload: { appointment: "APT-two", slot: "After" } });
    expect(annotations).not.toHaveBeenCalled();
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(compare).toHaveFocus();
    expect(revoke).not.toHaveBeenCalled();
    await userEvent.setup().click(disclosure);
    await waitFor(() => expect(revoke).toHaveBeenCalledTimes(2));
    expect(revoke).toHaveBeenCalledWith(before.getAttribute("src"));
    expect(revoke).toHaveBeenCalledWith(after.getAttribute("src"));
} finally {
    restore();
  }
});

it("shows two saved Encounter originals and zooms one without another request", async () => {
  const restore = installDialogMethods();
  const originals = vi.fn();
  let id = 0;
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = () => `blob:synthetic-encounter-photo-${++id}`;
    static override revokeObjectURL = vi.fn();
  });
  server.use(
    http.post(`${rpc}frappe.client.get`, () => record(first.id, "APT-one")),
    http.post(`${method}read_pair`, () => reply(pair("APT-one", metadata, metadata))),
    http.post(`${method}read_image`, async ({ request }) => {
      originals(await body(request));
      return new HttpResponse(Uint8Array.from([255, 216, 255, 0]), { headers: { "Content-Type": "image/jpeg" } });
    }),
  );
  try {
    setup(ROUTES.visit(first.id));
    const section = within(await screen.findByRole("region", { name: "Same-visit photos" }));
    expect(originals).not.toHaveBeenCalled();
    await openPanel(section);
    const view = await section.findByRole("button", { name: "View After photo" });
    expect(await section.findByRole("button", { name: "Compare pair" })).toBeVisible();
    expect(section.getByRole("img", { name: "Before photo" })).toBeVisible();
    const after = section.getByRole("img", { name: "After photo" });
    await userEvent.setup().click(view);
    const zoom = section.getByRole("dialog", { name: "After photo" });
    expect(within(zoom).getByRole("img", { name: "After photo" })).toHaveAttribute("src", after.getAttribute("src"));
    expect(originals).toHaveBeenCalledTimes(2);
    expect(originals).toHaveBeenCalledWith({ payload: { appointment: "APT-one", slot: "Before" } });
    expect(originals).toHaveBeenCalledWith({ payload: { appointment: "APT-one", slot: "After" } });
    fireEvent(zoom, new Event("cancel", { cancelable: true }));
    expect(view).toHaveFocus();
    fireEvent.error(after);
    expect(section.getByText("This photo can’t be opened.")).toBeVisible();
    expect(section.queryByRole("button", { name: "Compare pair" })).toBeNull();
    expect(section.getByRole("img", { name: "Before photo" })).toBeVisible();
  } finally {
    restore();
  }
});

it.each([
  ["no linked appointment", record(first.id, null), null, "Same-visit photo status is not available here."],
  ["different patient", record(first.id, "APT-one", "PAT-other"), null, "Same-visit photo status is not available here."],
  ["missing app", record(first.id, "APT-one"), new HttpResponse(null, { status: 404 }), "Same-visit photo status is not available here."],
  ["off or denied", record(first.id, "APT-one"), new HttpResponse(null, { status: 403 }), "Same-visit photo status is not available here."],
  ["different appointment", record(first.id, "APT-one"), reply(pair("APT-other", metadata, null)), "Same-visit photo status is not available here."],
  ["empty pair", record(first.id, "APT-one"), reply(pair("APT-one", null, null)), "No photos recorded for this visit."],
] as const)("fails closed in saved Encounter for %s", async (_, link, response, text) => {
  const readPair = vi.fn();
  const readImage = vi.fn();
  server.use(
    http.post(`${rpc}frappe.client.get`, () => link),
    http.post(`${method}read_pair`, () => {
      readPair();
      return response ?? HttpResponse.error();
    }),
    http.post(`${method}read_image`, () => { readImage(); return HttpResponse.error(); }),
  );
  setup(ROUTES.visit(first.id));
  const section = within(await screen.findByRole("region", { name: "Same-visit photos" }));
  await openPanel(section);
  expect(await section.findByText(text)).toBeVisible();
  expect(readPair).toHaveBeenCalledTimes(response ? 1 : 0);
  expect(section.queryByRole("button", { name: /view (before|after)/i })).toBeNull();
  expect(readImage).not.toHaveBeenCalled();
});

it.each([
  { fixture: false, docstatus: 1, enabled: true, native: false, role: "DOC", offerDrawing: false },
  { fixture: true, docstatus: 0, enabled: true, native: false, role: "DOC", offerDrawing: false },
  { fixture: false, docstatus: 0, enabled: false, native: false, role: "DOC", offerDrawing: false },
  { fixture: false, docstatus: 0, enabled: true, native: false, role: "DOC", offerDrawing: true },
  { fixture: false, docstatus: 0, enabled: true, native: true, role: "DOC", offerDrawing: true },
  { fixture: false, docstatus: 0, enabled: true, native: true, role: "REC", offerDrawing: false },
] as const)("gates linked drawing on native draft, role and capability ($fixture / $docstatus / $enabled / $native / $role)", async ({ fixture, docstatus, enabled, native, role, offerDrawing }) => {
  const read = vi.fn();
  const drawingRead = vi.fn<VisitAnnotationCallbacks["read"]>().mockResolvedValue(null);
  const drawingSubmit = vi.fn<VisitAnnotationCallbacks["submit"]>();
  const drawingStatus = vi.fn<VisitAnnotationCallbacks["status"]>();
  const nativeDrawingRead = vi.fn();
  const originalRead = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = vi.fn(() => "blob:synthetic-before");
    static override revokeObjectURL = vi.fn();
  });
  const version = "2026-11-20 09:00:00.000001";
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession(role)),
    http.get(`${rpc}clinic_ui.api.appointments.view`, () => reply({
      workspace_patient_id: first.patientId, appointment_id: ref, appointment_date: first.encounterDate,
      appointment_time: "09:30:00", status: "Open", modified: version,
      can_reschedule: false, can_checkin: false, is_fixture: fixture,
      ...(!fixture && { native_appointment_id: "APT-linked" }),
    })),
    http.get(`${rpc}clinic_ui.api.encounters.open_for_appointment`, () => reply({
      context: { appointment: "APT-linked", patient: first.patientId, practitioner: "PRA", status: "Open" },
      encounter: { id: first.id, modified: version, docstatus, symptoms: [], diagnoses: [] },
      notes: { state: "available", items: [], next_offset: null },
      medications: { state: "available", complete: true, items: [] },
    })),
    http.get(`${rpc}clinic_ui.api.lab_reports.list_for_encounter`, () => reply({ items: [], nextOffset: null })),
    http.post(`${method}read_pair`, async ({ request }) => {
      read(await body(request));
      return reply({ ...pair("APT-linked", metadata, null), ...(enabled && { drawings_enabled: true }) });
    }),
    http.post(`${rpc}clinic_photos.api.visit_annotations.read`, async ({ request }) => {
      nativeDrawingRead(await body(request));
      return new HttpResponse(null, { status: 403 });
    }),
    http.post(`${method}read_image`, async ({ request }) => {
      originalRead(await body(request));
      return new HttpResponse(Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0]), {
        headers: { "Content-Type": "image/png" },
      });
    }),
  );
  const path = ROUTES.linkedVisit(ref, first.id);
  renderWithDataRouter([{ path: ROUTES.visitPattern, element: <LinkedVisitWorkspace appointmentRef={ref} encounterId={first.id}
    annotationCallbacks={native ? undefined : { read: drawingRead, submit: drawingSubmit, status: drawingStatus }} /> }],
  { initialEntries: [path], role, sessionMode: "live" });
  const section = within(await screen.findByRole("region", { name: "Same-visit photos" }));
  if (fixture) {
    expect(section.queryByText("View visit photos")).toBeNull();
    expect(read).not.toHaveBeenCalled();
  } else {
    await openPanel(section);
    expect(await section.findByRole("button", { name: "View Before photo" })).toBeVisible();
    expect(read).toHaveBeenCalledExactlyOnceWith({ payload: { appointment: "APT-linked" } });
    expect(section.queryByRole("button", { name: "Annotate After photo" })).toBeNull();
    expect(drawingRead).not.toHaveBeenCalled();
    if (offerDrawing) {
      const restore = installDialogMethods();
      try {
        const open = section.getByRole("button", { name: "Annotate Before photo" });
        await userEvent.setup().click(open);
        const dialog = section.getByRole("dialog", { name: "Annotate Before photo" });
        expect(await within(dialog).findByText("Could not open this drawing.")).toBeVisible();
        expect(originalRead.mock.calls).toEqual([
          [{ payload: { appointment: "APT-linked", slot: "Before" } }],
          [{ payload: { appointment: "APT-linked", slot: "Before" } }],
        ]);
        if (native) {
          expect(nativeDrawingRead).toHaveBeenCalledExactlyOnceWith({ payload: { appointment: "APT-linked", slot: "Before" } });
        } else {
          expect(drawingRead).toHaveBeenCalledExactlyOnceWith({ appointment: "APT-linked", slot: "Before" }, expect.any(AbortSignal));
        }
        await userEvent.setup().click(within(dialog).getByRole("button", { name: "Close drawing" }));
        expect(open).toHaveFocus();
      } finally {
        restore();
      }
    } else expect(section.queryByRole("button", { name: "Annotate Before photo" })).toBeNull();
  }
  expect(drawingSubmit).not.toHaveBeenCalled();
  expect(drawingStatus).not.toHaveBeenCalled();
});

it("aborts a pending original on close and never publishes the late response", async () => {
  const create = vi.fn(() => "blob:late-visit-photo");
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = create;
    static override revokeObjectURL = revoke;
  });
  let release!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  const network = globalThis.fetch;
  const read = vi.fn((_init: RequestInit | undefined) => pending);
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) =>
    input === `${method}read_image` ? read(init) : network(input, init));
  server.use(
    http.post(`${rpc}frappe.client.get`, () => record(first.id, "APT-one")),
    http.post(`${method}read_pair`, () => reply(pair("APT-one", metadata, null))),
  );
  try {
    setup(ROUTES.visit(first.id));
    const disclosure = await openPanel();
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    expect(read.mock.calls[0]?.[0]).toMatchObject({
      credentials: "same-origin", mode: "same-origin", cache: "no-store", redirect: "error",
    });
    const signal = read.mock.calls[0]?.[0]?.signal;
    expect(signal?.aborted).toBe(false);
    await userEvent.setup().click(disclosure);
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await act(async () => release(new Response(Uint8Array.from([255, 216, 255, 0]), {
      headers: { "Content-Type": "image/jpeg" },
    })));
    expect(create).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: "Before photo" })).toBeNull();
  } finally {
    spy.mockRestore();
  }
});

it.each([
  ["denied", () => new HttpResponse(null, { status: 403 })],
  ["SVG", () => new HttpResponse("<svg/>", { headers: { "Content-Type": "image/svg+xml" } })],
  ["spoofed PNG", () => new HttpResponse("<html>Sign in</html>", { headers: { "Content-Type": "image/png" } })],
] as const)("rejects %s originals without producing a blob URL", async (_, response) => {
  const create = vi.fn(() => "blob:unexpected");
  vi.stubGlobal("URL", class extends URL { static override createObjectURL = create; });
  server.use(
    http.post(`${rpc}frappe.client.get`, () => record(first.id, "APT-one")),
    http.post(`${method}read_pair`, () => reply(pair("APT-one", metadata, null))),
    http.post(`${method}read_image`, response),
  );
  setup(ROUTES.visit(first.id));
  const section = within(await screen.findByRole("region", { name: "Same-visit photos" }));
  await openPanel(section);
  expect(await section.findByText("This photo can’t be opened.")).toBeVisible();
  expect(section.queryByRole("img")).toBeNull();
  expect(section.queryByRole("button", { name: "Compare pair" })).toBeNull();
  expect(create).not.toHaveBeenCalled();
});
