import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  PatientWriteOutcomeUnknownError,
  PatientWriteRejectedError,
  saveNativePatient,
} from "./patient-write";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const input = {
  first_name: "Test",
  last_name: "Patient",
  sex: "Other",
  dob: "2000-01-01",
  operation_key: "operation-1",
};

function success(
  status = "created",
  patient: Record<string, unknown> = {
    workspace_patient_id: "alias",
    display_name: "Test Patient",
    first_name: "Test",
    last_name: "Patient",
    sex: "Other",
    dob: "2000-01-01",
    status: "Active",
    modified: "version",
  },
) {
  return new Response(JSON.stringify({ message: { status, patient } }), {
    status: 200,
  });
}

describe("native patient writes", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it("posts one create request and never acknowledges", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(success());
    await saveNativePatient(input);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.patients.create",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      input,
    );
    expect(
      (fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>)[
        "X-Frappe-CSRF-Token"
      ],
    ).toBe("csrf");
    expect(localStorage.length).toBe(0);
  });

  it("ignores the retired browser fence key", async () => {
    localStorage.setItem("clinic.patient-write.pending", "1");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(success());
    await saveNativePatient(input);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts canonical backend fields that differ from what was submitted", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      success("created", {
        workspace_patient_id: "PAT-0001",
        display_name: "TEST  PATIENT",
        first_name: "TEST",
        last_name: "PATIENT",
        sex: "Female",
        dob: "2000-01-01",
        status: null,
        modified: "v2",
      }),
    );
    await expect(saveNativePatient(input)).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts a canonical alias that differs from the stored one on update", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      success("updated", {
        workspace_patient_id: "PAT-0001",
        display_name: "TEST PATIENT",
        status: "Active",
        modified: "v2",
      }),
    );
    await expect(
      saveNativePatient({
        ...input,
        workspace_patient_id: "legacy-alias",
        base_modified: "v1",
      }),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends an update with the alias and base version only", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      success("updated"),
    );
    await saveNativePatient({
      ...input,
      workspace_patient_id: "alias",
      base_modified: "old",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.patients.update",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      ...input,
      workspace_patient_id: "alias",
      base_modified: "old",
    });
  });

  it.each([
    ["missing status", { patient: { workspace_patient_id: "alias" } }],
    ["wrong status", { status: "updated", patient: { workspace_patient_id: "alias" } }],
    ["missing patient", { status: "created" }],
    ["ack-only payload", { status: "created", ackToken: "a".repeat(64) }],
    ["patient without alias", { status: "created", patient: { display_name: "T", modified: "v" } }],
    ["blank alias", { status: "created", patient: { workspace_patient_id: " ", display_name: "T", modified: "v" } }],
    ["patient without display name", { status: "created", patient: { workspace_patient_id: "alias", modified: "v" } }],
    ["patient without version", { status: "created", patient: { workspace_patient_id: "alias", display_name: "T", modified: " " } }],
    ["patient without status field", { status: "created", patient: { workspace_patient_id: "alias", display_name: "T", modified: "v" } }],
    ["blank status field", { status: "created", patient: { workspace_patient_id: "alias", display_name: "T", modified: "v", status: "" } }],
  ])("rejects a %s response as unknown", async (_case, message) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ message }), { status: 200 }),
      );
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a non-JSON success body as unknown", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("not json", { status: 200 }),
    );
    await expect(saveNativePatient(input)).rejects.toBeInstanceOf(
      PatientWriteOutcomeUnknownError,
    );
  });

  it.each([
    ["denied", 403, "FORBIDDEN"],
    ["validation", 422, "VALIDATION"],
    ["stale", 409, "STALE_VERSION"],
  ] as const)(
    "maps a %s refusal to a known failure with the native status and code",
    async (kind, status, code) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(
          new Response(JSON.stringify({ message: { code } }), { status }),
        );
      const failure = await saveNativePatient(input).catch((error) => error);
      expect(failure).toBeInstanceOf(PatientWriteRejectedError);
      expect(failure).toMatchObject({ kind, status, code });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("keeps an unclassified 409 as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: { code: "OPERATION_CONFLICT" } }), {
        status: 409,
      }),
    );
    await expect(saveNativePatient(input)).rejects.toBeInstanceOf(
      PatientWriteOutcomeUnknownError,
    );
  });

  it("keeps a lost connection as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("dropped"));
    await expect(saveNativePatient(input)).rejects.toBeInstanceOf(
      PatientWriteOutcomeUnknownError,
    );
  });

  it("keeps a malformed success as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: { status: "created" } }), {
        status: 200,
      }),
    );
    await expect(saveNativePatient(input)).rejects.toBeInstanceOf(
      PatientWriteOutcomeUnknownError,
    );
  });

  it("does not retry a stale refusal", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: { code: "STALE_VERSION" } }), {
        status: 409,
      }),
    );
    await expect(saveNativePatient(input)).rejects.toBeInstanceOf(
      PatientWriteRejectedError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses to write without a session token", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(saveNativePatient(input)).rejects.toBeDefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
