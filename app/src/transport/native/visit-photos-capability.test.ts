import { afterEach, expect, it, vi } from "vitest";
import { readVisitPhotoAppointment, readVisitPhotoPair } from "./visit-photos";
import { listHistoryNotes } from "./history-notes";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));
afterEach(() => vi.restoreAllMocks());

it.each([[undefined, false], [true, true]] as const)("decodes capability %s", async (flag, expected) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: {
    appointment: "APT", before: null, after: null, ...(flag && { drawings_enabled: flag }),
  } }), { status: 200 }));
  expect(await readVisitPhotoPair("APT", new AbortController().signal)).toMatchObject({ drawingsEnabled: expected });
});

it("rejects non-true capability", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: {
    appointment: "APT", before: null, after: null, drawings_enabled: false,
  } }), { status: 200 }));
  expect(await readVisitPhotoPair("APT", new AbortController().signal)).toBeNull();
});

it("binds opaque history photos on the server without raw resource fallback", async () => {
  const ref = `cpr1_${"a".repeat(803)}`;
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
    JSON.stringify({ message: { appointment: "APT-1" } }), { status: 200 },
  ));
  expect(await readVisitPhotoAppointment("ENC-1", ref, new AbortController().signal)).toBe("APT-1");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0]?.[0])).toContain("clinic_ui.api.encounters.history_photo_appointment");
  expect(String(fetcher.mock.calls[0]?.[1]?.body)).toContain(ref);
});

it("does not query legacy clinical notes for opaque patients", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch");
  await expect(listHistoryNotes("ENC-1", "cpr1_valid", 0, new AbortController().signal)).rejects.toMatchObject({
    code: "MALFORMED",
  });
  expect(fetcher).not.toHaveBeenCalled();
});

it("never falls back to generic encounter reads after opaque binding denial", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 403 }));
  expect(await readVisitPhotoAppointment("ENC-1", "cpr1_valid", new AbortController().signal)).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0]?.[0])).toContain("clinic_ui.api.encounters.history_photo_appointment");
});

it("preserves legacy encounter binding", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: {
    doctype: "Patient Encounter", name: "ENC-1", patient: "PAT-1", appointment: "APT-1",
  } }), { status: 200 }));
  expect(await readVisitPhotoAppointment("ENC-1", "PAT-1", new AbortController().signal)).toBe("APT-1");
  expect(String(fetcher.mock.calls[0]?.[0])).toContain("frappe.client.get");
});
