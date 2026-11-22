import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithDataRouter, setViewport } from "../../test/render";

const rpc = "/api/method/";
const photos = `${rpc}clinic_photos.api.visit_photos.`;
const upload = `${rpc}clinic_photos.api.visit_upload.`;
const options = `${rpc}clinic_photos.api.visit_options.capture_options`;
const ownBefore = `${photos}read_own_before`;
const ref = "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_abcdefghijklmnop";
const appointment = "APT-native";
const patientId = "PAT-native";
const encounter = "ENC-native";
const version = "2026-11-20 09:00:00.000001";
const snapshot = { photo: "INTERNAL-SYNTHETIC", body_area: "Left cheek", viewpoint: "Front" };
const reply = (message: unknown) => HttpResponse.json({ message });
const flags = (consent = false, before = false, after = false) => ({
  has_active_consent: consent, can_grant: !consent, can_revoke: consent, can_capture_before: before, can_capture_after: after,
});

async function payload(request: Request) {
  expect(request.headers.get("X-Frappe-CSRF-Token")).toBe("native-csrf");
  expect(new URL(request.url).search).toBe("");
  const body = await request.json() as { payload: Record<string, unknown> };
  expect(Object.keys(body)).toEqual(["payload"]);
  return body.payload;
}

const png = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0]);
const file = () => new File([png], "synthetic.png", { type: "image/png" });
const beforeImage = () => new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
const setup = (role: "REC" | "DOC" | "PHA", path = ROUTES.appointment(ref)) => {
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, { "read.appointment": "enabled", "read.patient": "enabled" })));
  return renderWithDataRouter(appRoutes, { initialEntries: [path], role, sessionMode: "live" });
};

async function open() {
  await userEvent.setup().click(await screen.findByText("View visit photos", { selector: "summary" }));
}

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  server.use(
    http.get(`${rpc}clinic_ui.api.appointments.view`, () => reply({
      workspace_patient_id: patientId, appointment_id: ref, appointment_date: "2026-11-20",
      appointment_time: "09:30:00", status: "Open", modified: version,
      can_reschedule: false, can_checkin: false, is_fixture: false, native_appointment_id: appointment,
    })),
    http.get(`${rpc}clinic_ui.api.lab_reports.list_for_appointment`, () => reply({ items: [], nextOffset: null })),
    http.get(`${rpc}clinic_ui.api.lab_reports.list_for_encounter`, () => reply({ items: [], nextOffset: null })),
    http.get(`${rpc}clinic_ui.api.encounters.open_for_appointment`, () => reply({
      context: { appointment, patient: patientId, practitioner: "PRA", status: "Closed" },
      encounter: { id: encounter, modified: version, docstatus: 0, symptoms: [], diagnoses: [] },
      notes: { state: "available", items: [], next_offset: null },
      medications: { state: "available", complete: true, items: [] },
    })),
    http.get(`${rpc}clinic_ui.api.encounters.view`, () => reply({
      id: encounter, patientId, patientName: "Synthetic Patient", practitioner: "PRA",
      encounterDate: "2026-11-20", docstatus: 0, symptoms: [], diagnoses: [],
    })),
    http.post(`${photos}read_pair`, () => reply({ appointment, before: null, after: null })),
    http.post(ownBefore, () => new HttpResponse(null, { status: 404 })),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  localStorage.removeItem(LOCALE_KEY);
});

it("lets reception attest and supply a source, then refreshes options; revocation keeps prior photos", async () => {
  setViewport(390);
  const writes = vi.fn();
  const reads = vi.fn();
  const create = vi.fn()
    .mockReturnValueOnce("blob:synthetic-reception-before")
    .mockReturnValueOnce("blob:synthetic-reception-after-revision");
  const dispose = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = create;
    static override revokeObjectURL = dispose;
  });
  let active = false;
  let hasBefore = false;
  server.use(
    http.post(options, async ({ request }) => {
      reads(await payload(request));
      return reply(flags(active, !hasBefore, true));
    }),
    http.post(ownBefore, () => hasBefore ? beforeImage() : new HttpResponse(null, { status: 404 })),
    http.post(`${photos}grant_consent`, async ({ request }) => {
      writes(await payload(request)); active = true; hasBefore = true;
      return reply({ ok: true });
    }),
    http.post(`${photos}revoke_consent`, async ({ request }) => {
      writes(await payload(request)); active = false;
      return reply({ ok: true });
    }),
  );
  const { unmount } = setup("REC");
  const intake = await screen.findByRole("region", { name: "Confirm patient & hand off" });
  const user = userEvent.setup();
  const verify = within(intake).getByRole("heading", { name: "1 · Verify patient and consent" }).parentElement!;
  const before = within(intake).getByRole("heading", { name: "2 · Before image" }).parentElement!;
  const checkbox = await within(verify).findByRole("checkbox", { name: "I confirm the patient consented to visit photos." });
  const grant = within(verify).getByRole("button", { name: "Record consent" });
  expect(checkbox).not.toBeChecked();
  expect(grant).toBeDisabled();
  expect(screen.queryByLabelText("Before photo file")).toBeNull();
  await user.type(screen.getByLabelText("Consent source"), "Verbal confirmation");
  expect(grant).toBeDisabled();
  await user.click(checkbox);
  await user.click(grant);
  expect(await within(before).findByRole("button", { name: "View Before photo" })).toBeVisible();
  expect(within(before).getByRole("img", { name: "Before photo" })).toHaveAttribute("src", "blob:synthetic-reception-before");
  const revoke = await within(verify).findByRole("button", { name: "Revoke consent" });
  expect(screen.queryByLabelText("After photo file")).toBeNull();
  await user.click(revoke);
  expect(await screen.findByText("Consent revoked. Existing photos remain available.")).toBeVisible();
  expect(await within(before).findByRole("img", { name: "Before photo" }))
    .toHaveAttribute("src", "blob:synthetic-reception-after-revision");
  expect(dispose).toHaveBeenCalledExactlyOnceWith("blob:synthetic-reception-before");
  expect(writes.mock.calls).toEqual([[{ appointment, attested: true, evidence: "Verbal confirmation" }], [{ appointment }]]);
  expect(reads.mock.calls).toEqual([[{ appointment }], [{ appointment }], [{ appointment }]]);
  unmount();
  expect(dispose.mock.calls).toEqual([["blob:synthetic-reception-before"], ["blob:synthetic-reception-after-revision"]]);
});

it("lets reception revoke with a protected Before photo without exposing preview or capture", async () => {
  const revoke = vi.fn().mockReturnValueOnce(new HttpResponse(null, { status: 503 })).mockImplementation(() => {
    active = false;
    return reply({ ok: true });
  });
  let active = true;
  const genericRead = vi.fn(() => beforeImage());
  const directRead = vi.fn(() => beforeImage());
  const denied = vi.fn(() => new HttpResponse(null, { status: 403 }));
  server.use(
    http.post(options, () => reply(flags(active))),
    http.post(ownBefore, async ({ request }) => {
      expect(await payload(request)).toEqual({ appointment });
      return denied();
    }),
    http.post(`${photos}read_pair`, genericRead),
    http.post(`${photos}read_image`, genericRead),
    http.get("/private/files/*", directRead),
    http.get("/api/resource/File/*", directRead),
    http.post(`${photos}revoke_consent`, async ({ request }) => revoke(await payload(request))),
  );
  setup("REC");
  expect(await screen.findByText("Same-visit photo status is not available here.")).toBeVisible();
  const user = userEvent.setup();
  const revokeButton = await screen.findByRole("button", { name: "Revoke consent" });
  expect(screen.queryByRole("button", { name: "View Before photo" })).toBeNull();
  expect(screen.queryByRole("img", { name: "Before photo" })).toBeNull();
  expect(screen.queryByLabelText("Before photo file")).toBeNull();
  await user.click(revokeButton);
  const check = await screen.findByRole("button", { name: "Check consent status" });
  expect(screen.queryByRole("button", { name: /record consent|revoke consent/i })).toBeNull();
  expect(screen.queryByLabelText("Before photo file")).toBeNull();
  expect(revoke).toHaveBeenCalledExactlyOnceWith({ appointment });
  await user.click(check);
  await user.click(await screen.findByRole("button", { name: "Revoke consent" }));
  expect(await screen.findByText("Consent revoked. Existing photos remain available.")).toBeVisible();
  expect(await screen.findByRole("button", { name: "Record consent" })).toBeDisabled();
  expect(revoke.mock.calls).toEqual([[{ appointment }], [{ appointment }]]);
  expect(denied).toHaveBeenCalled();
  expect(genericRead).not.toHaveBeenCalled();
  expect(directRead).not.toHaveBeenCalled();
});

it("disposes the owned image before loading a different native appointment", async () => {
  const nextRef = ref.replace("0123", "1123");
  const nextAppointment = "APT-native-next";
  const create = vi.fn(() => "blob:synthetic-first-appointment");
  const dispose = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = create;
    static override revokeObjectURL = dispose;
  });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const beforeRead = vi.fn();
  server.use(
    http.get(`${rpc}clinic_ui.api.appointments.view`, ({ request }) => {
      const next = new URL(request.url).searchParams.get("workspace_appointment_id") === nextRef;
      return reply({
        workspace_patient_id: patientId, appointment_id: next ? nextRef : ref, appointment_date: "2026-11-20",
        appointment_time: "09:30:00", status: "Open", modified: version,
        can_reschedule: false, can_checkin: false, is_fixture: false,
        native_appointment_id: next ? nextAppointment : appointment,
      });
    }),
    http.post(options, () => reply(flags())),
    http.post(ownBefore, async ({ request }) => {
      const input = await payload(request);
      beforeRead(input);
      if (input.appointment === appointment) return beforeImage();
      await pending;
      return new HttpResponse(null, { status: 404 });
    }),
  );
  const { router } = setup("REC");
  const firstImage = await screen.findByRole("img", { name: "Before photo" });
  expect(firstImage).toHaveAttribute("src", "blob:synthetic-first-appointment");
  try {
    await act(async () => { await router.navigate(ROUTES.appointment(nextRef)); });
    await waitFor(() => expect(beforeRead.mock.calls).toEqual([[{ appointment }], [{ appointment: nextAppointment }]]));
    expect(screen.queryByRole("img", { name: "Before photo" })).toBeNull();
    expect(dispose).toHaveBeenCalledExactlyOnceWith("blob:synthetic-first-appointment");
  } finally {
    release();
  }
  expect(await screen.findByText("Same-visit photo status is not available here.")).toBeVisible();
  expect(create).toHaveBeenCalledTimes(1);
});

it.each(["REC", "DOC"] as const)("%s uploads Before once and checks only the original key after an unknown outcome", async (role) => {
  const saved = vi.fn();
  const checked = vi.fn();
  const bytes = vi.fn();
  const ownReads = vi.fn();
  const pairReads = vi.fn();
  const create = vi.fn(() => "blob:synthetic-owned-before");
  const dispose = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL = create;
    static override revokeObjectURL = dispose;
  });
  let published = false;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let showImage!: () => void;
  const reading = new Promise<void>((resolve) => { showImage = resolve; });
  server.use(
    http.post(options, () => reply(flags(true, !published))),
    http.post(`${photos}read_pair`, () => {
      pairReads();
      return reply({ appointment, before: published ? snapshot : null, after: null });
    }),
    http.post(ownBefore, async ({ request }) => {
      ownReads(await payload(request));
      expect(request.headers.get("Accept")).toBe("image/png, image/jpeg");
      if (!published) return new HttpResponse(null, { status: 404 });
      await reading;
      return beforeImage();
    }),
    http.post(`${photos}read_image`, () => { bytes(); return HttpResponse.error(); }),
    http.post(`${upload}capture`, async ({ request }) => {
      saved(await payload(request));
      await pending;
      return HttpResponse.error();
    }),
    http.post(`${upload}capture_status`, async ({ request }) => {
      checked(await payload(request));
      if (checked.mock.calls.length === 1) return reply({ state: "not_found" });
      published = true;
      return reply({ state: "saved" });
    }),
  );
  const { unmount } = setup(role);
  if (role === "DOC") await open();
  const photoScope = role === "REC"
    ? within((await screen.findByRole("heading", { name: "2 · Before image" })).parentElement!)
    : screen;
  const user = userEvent.setup();
  await user.upload(await photoScope.findByLabelText("Before photo file"), file());
  await user.type(screen.getByLabelText("Body area"), "Left cheek");
  await user.type(screen.getByLabelText("Viewpoint"), "Front");
  await user.click(screen.getByRole("button", { name: "Save Before photo" }));
  expect(await screen.findByText("Sending photo…")).toBeVisible();
  await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
  const original = saved.mock.calls[0]?.[0];
  expect(original).toEqual({
    appointment, slot: "Before", body_area: "Left cheek", viewpoint: "Front",
    request_key: expect.stringMatching(/^[A-Za-z0-9_-]{16,128}$/), image_base64: "iVBORw0KGgoA",
  });
  expect(checked).not.toHaveBeenCalled();
  if (role === "DOC") {
    const summary = screen.getByText("View visit photos", { selector: "summary" });
    await user.click(summary);
    await waitFor(() => expect(screen.queryByText("Sending photo…")).toBeNull());
    await user.click(summary);
    expect(await screen.findByText("Sending photo…")).toBeVisible();
  }
  expect(screen.queryByRole("button", { name: "Save Before photo" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Check upload status" })).toBeNull();
  release();
  expect(await screen.findByText("Upload outcome is not confirmed.")).toBeVisible();
  expect(await screen.findByRole("button", { name: "Check upload status" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Save Before photo" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Check upload status" }));
  expect(await screen.findByText("No upload record confirmed yet.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Save Before photo" })).toBeNull();
  expect(ownReads.mock.calls).toEqual(role === "REC" ? [[{ appointment }]] : []);
  await user.click(screen.getByRole("button", { name: "Check upload status" }));
  if (role === "REC") {
    await waitFor(() => expect(ownReads.mock.calls).toEqual([[{ appointment }], [{ appointment }]]));
    expect(photoScope.queryByText("No Before photo recorded for this appointment.")).toBeNull();
    expect(photoScope.queryByRole("img", { name: "Before photo" })).toBeNull();
    showImage();
    expect(await photoScope.findByRole("img", { name: "Before photo" })).toHaveAttribute("src", "blob:synthetic-owned-before");
    expect(photoScope.queryByRole("img", { name: "After photo" })).toBeNull();
    expect(pairReads).not.toHaveBeenCalled();
    const restore = installDialogMethods();
    try {
      const view = photoScope.getByRole("button", { name: "View Before photo" });
      await user.click(view);
      const dialog = screen.getByRole("dialog", { name: "Before photo" });
      expect(within(dialog).getByRole("img", { name: "Before photo" })).toHaveAttribute("src", "blob:synthetic-owned-before");
      await user.click(within(dialog).getByRole("button", { name: "Close" }));
      expect(view).toHaveFocus();
    } finally {
      restore();
    }
    expect(create).toHaveBeenCalledTimes(1);
    expect(ownReads).toHaveBeenCalledTimes(2);
  } else {
    expect(ownReads).not.toHaveBeenCalled();
  }
  expect(await screen.findByRole("button", { name: "View Before photo" })).toBeVisible();
  expect(saved).toHaveBeenCalledTimes(1);
  expect(checked.mock.calls).toEqual(Array.from({ length: 2 }, () => [{
    appointment, slot: "Before", request_key: original.request_key,
  }]));
  expect(bytes).not.toHaveBeenCalled();
  unmount();
  if (role === "REC") expect(dispose).toHaveBeenCalledExactlyOnceWith("blob:synthetic-owned-before");
});

it.each(["DOC", "REC"] as const)("offers After only to a doctor on the linked saved draft (%s)", async (role) => {
  const lookedUp = vi.fn();
  const captured = vi.fn();
  const ownReads = vi.fn();
  server.use(
    http.post(options, async ({ request }) => {
      lookedUp(await payload(request));
      return reply(flags(true, false, true));
    }),
    http.post(`${photos}read_pair`, () => reply({ appointment, before: snapshot, after: null })),
    http.post(ownBefore, () => { ownReads(); return beforeImage(); }),
    http.post(`${photos}read_image`, () => new HttpResponse(null, { status: 403 })),
    http.post(`${upload}capture`, async ({ request }) => { captured(await payload(request)); return reply({ state: "pending" }); }),
  );
  setup(role, ROUTES.linkedVisit(ref, encounter));
  await open();
  if (role === "DOC") expect(await screen.findByText("This photo can’t be opened.")).toBeVisible();
  else expect(await screen.findByRole("button", { name: "View Before photo" })).toBeVisible();
  expect(await screen.findByRole("button", { name: "Revoke consent" })).toBeVisible();
  expect(lookedUp.mock.calls).toEqual([[role === "DOC" ? { appointment, encounter } : { appointment }]]);
  expect(ownReads).not.toHaveBeenCalled();
  if (role === "REC") {
    expect(screen.queryByLabelText("After photo file")).toBeNull();
    return;
  }
  const user = userEvent.setup();
  await user.upload(screen.getByLabelText("After photo file"), file());
  expect(screen.getByText("Left cheek")).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "Body area" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Viewpoint" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save After photo" }));
  expect(await screen.findByText("Upload queued. Check status to confirm it was saved.")).toBeVisible();
  expect(captured).toHaveBeenCalledExactlyOnceWith({
    appointment, encounter, slot: "After", body_area: "Left cheek", viewpoint: "Front",
    request_key: expect.any(String), image_base64: "iVBORw0KGgoA",
  });
});

it("falls back to read-only photos when capture options are off", async () => {
  server.use(
    http.post(options, () => new HttpResponse(null, { status: 403 })),
    http.post(`${photos}read_pair`, () => reply({ appointment, before: snapshot, after: null })),
  );
  setup("DOC");
  await open();
  expect(await screen.findByRole("button", { name: "View Before photo" })).toBeVisible();
  const panel = within(screen.getByRole("region", { name: "Same-visit photos" }));
  expect(await panel.findByText("Photo capture and consent controls are unavailable.")).toBeVisible();
  expect(panel.queryByRole("button", { name: /record consent|save before/i })).toBeNull();
});

it.each(["history", "other staff role"] as const)("makes no Reception-only read, options or mutation requests in %s", async (surface) => {
  const prohibited = vi.fn();
  const authorize = () => { prohibited(); return reply(flags(true, true, true)); };
  server.use(
    http.get(`${rpc}clinic_ui.api.patients.view`, () => reply({
      workspace_patient_id: patientId, display_name: "Synthetic Patient",
      first_name: null, last_name: null, sex: null, dob: null, status: null,
    })),
    http.get(`${rpc}clinic_ui.api.encounters.list_for_patient`, () => reply({
      items: [{ id: encounter, patientId, patientName: "Synthetic Patient", practitioner: "PRA", encounterDate: "2026-11-20", docstatus: 0 }],
      nextOffset: null,
    })),
    http.post(`${rpc}frappe.client.get`, () => reply({
      doctype: "Patient Encounter", name: encounter, patient: patientId, appointment,
    })),
    http.post(options, authorize),
    http.post(ownBefore, authorize),
    http.post(`${photos}grant_consent`, authorize),
    http.post(`${photos}revoke_consent`, authorize),
    http.post(`${upload}capture`, authorize),
    http.post(`${upload}capture_status`, authorize),
    http.post(`${photos}read_pair`, () => reply({ appointment, before: snapshot, after: null })),
    http.post(`${photos}read_image`, () => new HttpResponse(null, { status: 403 })),
  );
  if (surface === "history") {
    setup("DOC", ROUTES.patientHistory(patientId));
    await open();
    expect(await screen.findByText("This photo can’t be opened.")).toBeVisible();
  } else {
    setup("PHA");
    expect(await screen.findByRole("heading", { name: "Permission denied" })).toBeVisible();
  }
  expect(screen.queryByRole("button", { name: /record consent|revoke consent|save (before|after)|check upload/i })).toBeNull();
  expect(prohibited).not.toHaveBeenCalled();
});

it.each(["fixture", "unconfirmed native identity", "completed linked encounter"] as const)(
  "does not request capture options for %s", async (context) => {
    const read = vi.fn();
    server.use(
      http.get(`${rpc}clinic_ui.api.appointments.view`, () => reply({
        workspace_patient_id: patientId, appointment_id: ref, appointment_date: "2026-11-20",
        appointment_time: "09:30:00", status: "Closed", modified: version,
        can_reschedule: false, can_checkin: false, is_fixture: context === "fixture",
        ...(context === "completed linked encounter" && { native_appointment_id: appointment }),
      })),
      http.get(`${rpc}clinic_ui.api.encounters.open_for_appointment`, () => reply({
        context: { appointment, patient: patientId, practitioner: "PRA", status: "Closed" },
        encounter: { id: encounter, modified: version, docstatus: 1, symptoms: [], diagnoses: [] },
        notes: { state: "available", items: [], next_offset: null },
        medications: { state: "available", complete: true, items: [] },
      })),
      http.post(options, () => { read(); return reply(flags(true, true, true)); }),
    );
    setup("DOC", context === "completed linked encounter" ? ROUTES.linkedVisit(ref, encounter) : ROUTES.appointment(ref));
    if (context === "completed linked encounter") {
      await open();
      expect(await screen.findByText("No photos recorded for this visit.")).toBeVisible();
    } else {
      expect(await screen.findByRole("heading", { name: "Appointment details" })).toBeVisible();
      expect(screen.queryByText("View visit photos", { selector: "summary" })).toBeNull();
    }
    expect(read).not.toHaveBeenCalled();
  },
);

it("rejects oversize files and allocates a fresh key only for a new explicit attempt after confirmed rejection", async () => {
  const sent = vi.fn();
  const status = vi.fn();
  server.use(
    http.post(options, () => reply(flags(true, true))),
    http.post(`${upload}capture`, async ({ request }) => { sent(await payload(request)); return reply({ state: "pending" }); }),
    http.post(`${upload}capture_status`, async ({ request }) => {
      status(await payload(request));
      return reply({ state: "rejected" });
    }),
  );
  setup("DOC");
  await open();
  const user = userEvent.setup();
  const submit = async (selected: File) => {
    await user.upload(await screen.findByLabelText("Before photo file"), selected);
    await user.type(screen.getByLabelText("Body area"), "Left cheek");
    await user.type(screen.getByLabelText("Viewpoint"), "Front");
    await user.click(screen.getByRole("button", { name: "Save Before photo" }));
  };
  const large = new File([new Uint8Array(8 * 1024 * 1024 + 1)], "large.png", { type: "image/png" });
  await submit(large);
  expect(await screen.findByText("Choose a valid PNG or JPEG image, up to 8 MiB.")).toBeVisible();
  expect(sent).not.toHaveBeenCalled();
  await submit(file());
  await user.click(await screen.findByRole("button", { name: "Check upload status" }));
  expect(await screen.findByText("Photo was not saved. Check the details before making a new attempt.")).toBeVisible();
  expect(status).toHaveBeenCalledExactlyOnceWith({ appointment, slot: "Before", request_key: sent.mock.calls[0]?.[0].request_key });
  expect(sent).toHaveBeenCalledTimes(1);
  await submit(file());
  await screen.findByRole("button", { name: "Check upload status" });
  expect(sent).toHaveBeenCalledTimes(2);
  expect(sent.mock.calls[1]?.[0].request_key).not.toBe(sent.mock.calls[0]?.[0].request_key);
});
