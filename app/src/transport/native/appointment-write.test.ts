import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelNativeAppointment,
  checkinNativeAppointment,
  noShowNativeAppointment,
  rescheduleNativeAppointment,
} from "./appointment-write";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const ALIAS = "appointment-alias-1";
const OPERATION = "operation-key-123456";
const DETAIL = {
  workspace_patient_id: "WS-1",
  appointment_id: ALIAS,
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Scheduled",
  modified: "2026-11-20 09:00:00.000000",
};
const NATIVE_DETAIL = {
  workspace_patient_id: "PAT-0001",
  appointment_id: ALIAS,
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Scheduled",
  modified: "2026-11-20 09:00:00.000000",
};

function success(
  status: "rescheduled" | "checked_in" | "cancelled" | "no_show",
  appointment: Record<string, unknown> = DETAIL,
) {
  return new Response(
    JSON.stringify({ message: { status, appointment } }),
    { status: 200 },
  );
}

function rescheduleInput() {
  return {
    operation_key: OPERATION,
    workspace_appointment_id: ALIAS,
    base_modified: "2026-11-20 09:00:00.000000",
    appointment_date: "2026-11-21",
    appointment_time: "10:15",
  };
}

function checkinInput() {
  return {
    operation_key: OPERATION,
    workspace_appointment_id: ALIAS,
    base_modified: "2026-11-20 09:00:00.000000",
  };
}

function cancelInput() {
  return {
    operation_key: OPERATION,
    patient: "WS-1",
    appointment_id: ALIAS,
    reason: "Patient called to cancel.",
  };
}

describe("native appointment writes", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it("ignores the retired browser fence key and does not recreate it", async () => {
    localStorage.setItem("clinic.appointment-write.pending", "1");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(success("rescheduled"));

    await rescheduleNativeAppointment(rescheduleInput());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("clinic.appointment-write.pending")).toBe("1");
  });

  it("accepts native identifier values in the canonical fields", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(success("rescheduled", NATIVE_DETAIL));

    await expect(rescheduleNativeAppointment(rescheduleInput())).resolves.toEqual({
      status: "rescheduled",
      appointment: {
        workspace_patient_id: "PAT-0001",
        appointment_id: ALIAS,
        appointment_date: "2026-11-20",
        appointment_time: "09:30:00",
        status: "Scheduled",
        modified: "2026-11-20 09:00:00.000000",
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["an extra native appointment field", { ...DETAIL, native_appointment_id: "APT-1" }],
    ["an extra native patient field", { ...DETAIL, native_patient_id: "PAT-1" }],
    ["a raw patient field", { ...DETAIL, patient: "PAT-1" }],
    ["an unknown extra field", { ...DETAIL, extra: true }],
    [
      "only raw backend fields",
      {
        name: "APT-1",
        patient: "PAT-1",
        appointment_date: "2026-11-20",
        appointment_time: "09:30:00",
        status: "Scheduled",
        modified: "2026-11-20 09:00:00.000000",
      },
    ],
  ])("rejects %s as an unknown outcome", async (_label, appointment) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        success("rescheduled", appointment as Record<string, unknown>),
      );

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a success envelope with an acknowledgement token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          message: {
            status: "rescheduled",
            ackToken: "a".repeat(64),
            appointment: DETAIL,
          },
        }),
        { status: 200 },
      ),
    );

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fails closed without a CSRF token and never dispatches", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats a lost response as an unknown outcome and never retries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("network"));

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });

  it.each([
    ["missing appointment", { message: { status: "rescheduled" } }],
    ["unreadable appointment", { message: { status: "rescheduled", appointment: null } }],
    [
      "missing version",
      { message: { status: "rescheduled", appointment: { ...DETAIL, modified: undefined } } },
    ],
    [
      "invalid date",
      { message: { status: "rescheduled", appointment: { ...DETAIL, appointment_date: "2026-02-30" } } },
    ],
    [
      "wrong operation status",
      { message: { status: "checked_in", appointment: DETAIL } },
    ],
  ])("reports %s as an unknown outcome", async (_label, body) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [409, "STALE_VERSION", "stale"],
    [409, "WRITE_UNCERTAIN", "uncertain"],
    [422, "VALIDATION", "validation"],
    [403, "FORBIDDEN", "forbidden"],
    [401, "AUTH", "auth"],
    [500, "SERVER", "uncertain"],
  ])("maps status %i code %s to %s without retrying", async (status, code, kind) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ message: { code } }), { status }),
      );

    await expect(rescheduleNativeAppointment(rescheduleInput())).rejects.toMatchObject({
      code: kind,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends only the reschedule fields and checks the echoed detail", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(success("rescheduled", { ...DETAIL, appointment_date: "2026-11-21" }));

    await expect(
      rescheduleNativeAppointment(rescheduleInput()),
    ).resolves.toMatchObject({ status: "rescheduled" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments.reschedule",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      workspace_appointment_id: ALIAS,
      base_modified: "2026-11-20 09:00:00.000000",
      appointment_date: "2026-11-21",
      appointment_time: "10:15",
    });
  });

  it("treats a detail that names another appointment as unknown", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        success("rescheduled", { ...DETAIL, appointment_id: "other-alias" }),
      );

    await expect(
      rescheduleNativeAppointment(rescheduleInput()),
    ).rejects.toMatchObject({ code: "uncertain" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("checks in with the target and base version only", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        success("checked_in", { ...DETAIL, status: "Checked In" }),
      );

    await expect(
      checkinNativeAppointment(checkinInput()),
    ).resolves.toMatchObject({ status: "checked_in" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      workspace_appointment_id: ALIAS,
      base_modified: "2026-11-20 09:00:00.000000",
    });
  });

  it("cancels through the cancel endpoint and returns the cancelled detail", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      success("cancelled", {
        ...NATIVE_DETAIL,
        appointment_id: ALIAS,
        status: "Cancelled",
      }),
    );

    await expect(cancelNativeAppointment(cancelInput())).resolves.toEqual({
      status: "cancelled",
      appointment: {
        workspace_patient_id: "PAT-0001",
        appointment_id: ALIAS,
        appointment_date: "2026-11-20",
        appointment_time: "09:30:00",
        status: "Cancelled",
        modified: "2026-11-20 09:00:00.000000",
      },
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments_cancel_write.cancel",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      patient: "WS-1",
      appointment_id: ALIAS,
      reason: "Patient called to cancel.",
    });
  });

  it("records a no-show through its own endpoint", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        success("no_show", { ...DETAIL, status: "No Show" }),
      );

    await expect(
      noShowNativeAppointment(cancelInput()),
    ).resolves.toMatchObject({ status: "no_show" });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments_cancel_write.no_show",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      patient: "WS-1",
      appointment_id: ALIAS,
      reason: "Patient called to cancel.",
    });
  });

  it.each([
    ["empty", ""],
    ["blank", "   "],
    ["too long", "x".repeat(201)],
    ["multiline", "line\nbreak"],
  ])("refuses a %s reason before dispatching", async (_label, reason) => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(
      cancelNativeAppointment({ ...cancelInput(), reason }),
    ).rejects.toMatchObject({ code: "validation" });
    await expect(
      noShowNativeAppointment({ ...cancelInput(), reason }),
    ).rejects.toMatchObject({ code: "validation" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not retry or acknowledge after a refused cancel", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ message: { code: "STALE_VERSION" } }), {
          status: 409,
        }),
      );

    await expect(
      cancelNativeAppointment(cancelInput()),
    ).rejects.toMatchObject({ code: "stale" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });
});
