import { beforeEach, expect, it, vi } from "vitest";
import { createNativeEncounterDraft, readNativeEncounterPractitioner, searchNativeEncounterPatients } from "./encounter-draft";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const input = { userId: "doctor@example.com", patientId: "P1", practitionerId: "H1", appointmentTypeId: "T1" };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

beforeEach(() => { vi.restoreAllMocks(); });

it("does not search for blank patient input", async () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  expect(await searchNativeEncounterPatients("  ")).toEqual([]);
  expect(fetch).not.toHaveBeenCalled();
});

it("requires exactly one active practitioner", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ data: [] }));
  await expect(readNativeEncounterPractitioner(input.userId)).rejects.toMatchObject({ reason: "unavailable" });
});

it("creates once and verifies persisted draft without inventing status", async () => {
  const fetch = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(json({ message: { id: "E1", docstatus: 0 } }))
    .mockResolvedValueOnce(json({ data: { name: "E1", patient: "P1", practitioner: "H1", appointment_type: "T1", docstatus: 0, submit_orders_on_save: 0, drug_prescription: [], procedure_prescription: [], lab_test_prescription: [], therapies: [] } }));
  expect(await createNativeEncounterDraft(input)).toEqual({ id: "E1" });
  expect(fetch).toHaveBeenCalledTimes(2);
  const [post, readback] = fetch.mock.calls;
  expect(post?.[0]).toBe("/api/method/clinic_ui.api.encounters.create_draft");
  expect(post?.[1]).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": "csrf" } });
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ patient: "P1", practitioner: "H1", appointment_type: "T1" });
  expect(readback?.[0]).toBe("/api/resource/Patient%20Encounter/E1");
  expect(readback?.[1]).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error" });
  expect(readback?.[1]?.method ?? "GET").toBe("GET");
});

it("does not retry or fall back after ambiguous post", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("network"));
  await expect(createNativeEncounterDraft(input)).rejects.toMatchObject({ reason: "unconfirmed" });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([[403, "denied"], [409, "conflict"], [500, "unconfirmed"]] as const)("maps POST %i to %s", async (status, reason) => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({}, status));
  await expect(createNativeEncounterDraft(input)).rejects.toMatchObject({ reason });
  expect(fetch).toHaveBeenCalledTimes(1);
});
