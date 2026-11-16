import { beforeEach, describe, expect, it, vi } from "vitest";
import { patientWriteLocked, saveNativePatient } from "./patient-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const input = { first_name: "Test", last_name: "Patient", sex: "Other", dob: "2000-01-01", operation_key: "operation-1" };
const ackToken = "a".repeat(64);
function success(status = "created") {
  return new Response(JSON.stringify({ message: {
    status, ackToken, patient: { first_name: input.first_name, last_name: input.last_name, sex: input.sex, dob: input.dob, workspace_patient_id: "alias", display_name: "Test Patient", status: "Active", modified: "version" },
  } }), { status: 200 });
}

describe("native patient writes", () => {
  beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

  it("locks before dispatch and keeps lock after an uncertain response", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      expect(patientWriteLocked()).toBe(true);
      throw new Error("network");
    });
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
    await expect(saveNativePatient({ ...input, operation_key: "operation-2" })).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("locks through create and ack and clears only after validated acknowledgement", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      expect(patientWriteLocked()).toBe(true);
      expect(localStorage.getItem("clinic.patient-write.pending")).toBe("1");
      expect(JSON.stringify(localStorage)).not.toContain(ackToken);
      expect((init?.headers as Record<string, string>)["X-Frappe-CSRF-Token"]).toBe("csrf");
      if (String(url).endsWith(".create")) {
        expect(JSON.parse(String(init?.body))).toEqual(input);
        return success();
      }
      expect(String(url)).toBe("/api/method/clinic_ui.api.patients.ack");
      expect(JSON.parse(String(init?.body))).toEqual({ operation_key: input.operation_key, kind: "create", ack_token: ackToken });
      return new Response(JSON.stringify({ message: { acknowledged: true } }), { status: 200 });
    });
    await saveNativePatient(input);
    expect(patientWriteLocked()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("sends update acknowledgement using the same operation key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(success("updated")).mockResolvedValueOnce(new Response(JSON.stringify({ message: { acknowledged: true } }), { status: 200 }));
    await saveNativePatient({ ...input, workspace_patient_id: "alias", base_modified: "old" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\.update$/);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({ operation_key: input.operation_key, kind: "update", ack_token: ackToken });
  });

  it("acknowledges valid patient success with null status", async () => {
    const response = await success().json();
    response.message.patient.status = null;
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: { acknowledged: true } }), { status: 200 }));
    await saveNativePatient(input);
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe("/api/method/clinic_ui.api.patients.ack");
    expect(patientWriteLocked()).toBe(false);
  });

  it.each(["409", "connection", "invalid", "wrong-status"])("keeps lock after ack %s and does not post again", async (failure) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(success());
    if (failure === "connection") fetchMock.mockRejectedValueOnce(new Error("dropped"));
    else fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ message: failure === "invalid" ? { acknowledged: false } : { acknowledged: true } }), { status: failure === "409" ? 409 : failure === "wrong-status" ? 201 : 200 }));
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["", "A".repeat(64), "a".repeat(63)])("does not acknowledge invalid token", async (token) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: { status: "created", ackToken: token, patient: {} } }), { status: 200 }));
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not acknowledge a response with invalid patient data", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: { status: "created", ackToken, patient: {} } }), { status: 200 }));
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("treats malformed success as uncertain", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: { status: "created" } }), { status: 200 }));
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
  });

  it.each(["WRITE_LOCKED", "OPERATION_CONFLICT", "STALE_VERSION"])("keeps the browser locked after 409 %s without retrying", async (code) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: { code } }), { status: 409 }));
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(patientWriteLocked()).toBe(true);
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
