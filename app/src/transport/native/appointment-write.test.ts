import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  appointmentWriteLocked,
  bookNativeAppointment,
  cancelNativeAppointment,
  checkinNativeAppointment,
  NativeAppointmentWriteError,
  noShowNativeAppointment,
  rescheduleNativeAppointment,
} from "./appointment-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const TOKEN = "a".repeat(64);
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

function success(
  status: "booked" | "rescheduled" | "checked_in" | "cancelled" | "no_show",
  appointment: Record<string, unknown> = DETAIL,
) {
  return new Response(
    JSON.stringify({
      message: { status, ackToken: TOKEN, appointment },
    }),
    { status: 200 },
  );
}

function acknowledged() {
  return new Response(JSON.stringify({ message: { acknowledged: true } }), {
    status: 200,
  });
}

function bookInput() {
  return {
    operation_key: OPERATION,
    workspace_patient_id: "WS-1",
    appointment_date: "2026-11-20",
    appointment_time: "09:30",
  };
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
    vi.restoreAllMocks();
  });

  it("locks before dispatch and keeps the lock after an uncertain response", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        expect(appointmentWriteLocked()).toBe(true);
        throw new Error("network");
      });

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(appointmentWriteLocked()).toBe(true);

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "locked",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("books, acknowledges once and clears the lock only after the acknowledgement", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(async (url, init) => {
        expect(appointmentWriteLocked()).toBe(true);
        expect(String(url)).toBe(
          "/api/method/clinic_ui.api.appointments.book",
        );
        expect(JSON.parse(String(init?.body))).toEqual({
          operation_key: OPERATION,
          workspace_patient_id: "WS-1",
          appointment_date: "2026-11-20",
          appointment_time: "09:30",
        });
        expect(
          (init?.headers as Record<string, string>)["X-Frappe-CSRF-Token"],
        ).toBe("csrf");
        return success("booked");
      })
      .mockImplementationOnce(async (url, init) => {
        expect(appointmentWriteLocked()).toBe(true);
        expect(String(url)).toBe(
          "/api/method/clinic_ui.api.appointments.ack",
        );
        expect(JSON.parse(String(init?.body))).toEqual({
          operation_key: OPERATION,
          kind: "book",
          ack_token: TOKEN,
        });
        expect(JSON.stringify(localStorage)).not.toContain(TOKEN);
        return acknowledged();
      });

    await expect(bookNativeAppointment(bookInput())).resolves.toEqual({
      status: "booked",
      appointment: DETAIL,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(appointmentWriteLocked()).toBe(false);
  });

  it("sends only the reschedule fields and binds the acknowledgement alias", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("rescheduled", DETAIL))
      .mockResolvedValueOnce(acknowledged());

    await rescheduleNativeAppointment(rescheduleInput());

    expect(
      JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)),
    ).toEqual({
      operation_key: OPERATION,
      workspace_appointment_id: ALIAS,
      base_modified: "2026-11-20 09:00:00.000000",
      appointment_date: "2026-11-21",
      appointment_time: "10:15",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      kind: "reschedule",
      ack_token: TOKEN,
    });
  });

  it("checks in with the alias and base version only", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("checked_in", { ...DETAIL, status: "Checked In" }))
      .mockResolvedValueOnce(acknowledged());

    await checkinNativeAppointment(checkinInput());

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      workspace_appointment_id: ALIAS,
      base_modified: "2026-11-20 09:00:00.000000",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      kind: "checkin",
      ack_token: TOKEN,
    });
  });

  it.each([
    ["extra native identifier", { ...DETAIL, native_appointment_id: "native-1" }],
    ["missing version", { ...DETAIL, modified: undefined }],
    ["invalid version", { ...DETAIL, modified: "not-a-version" }],
    ["invalid date", { ...DETAIL, appointment_date: "2026-02-30" }],
  ])("treats a success with %s as uncertain and never acknowledges", async (_label, appointment) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("booked", appointment as Record<string, unknown>));

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(appointmentWriteLocked()).toBe(true);
  });

  it.each([
    ["wrong status", { message: { status: "rescheduled", ackToken: TOKEN, appointment: DETAIL } }],
    ["invalid token", { message: { status: "booked", ackToken: "A".repeat(64), appointment: DETAIL } }],
    ["extra envelope field", { message: { status: "booked", ackToken: TOKEN, appointment: DETAIL, extra: true } }],
  ])("fails closed on %s", async (_label, body) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(body), { status: 200 }),
      );

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(appointmentWriteLocked()).toBe(true);
  });

  it("keeps the lock after an uncertain acknowledgement and does not retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("booked"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: { acknowledged: false } }), {
          status: 200,
        }),
      );

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(appointmentWriteLocked()).toBe(true);

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "locked",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the lock after a stale version conflict and maps the outcome", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: { code: "STALE_VERSION" } }), {
          status: 409,
        }),
      );

    await expect(
      rescheduleNativeAppointment(rescheduleInput()),
    ).rejects.toBeInstanceOf(NativeAppointmentWriteError);

    await expect(
      rescheduleNativeAppointment(rescheduleInput()),
    ).rejects.toMatchObject({ code: "locked" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(appointmentWriteLocked()).toBe(true);
  });

  it("maps forbidden and validation without acknowledging", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: { code: "VALIDATION" } }), {
          status: 422,
        }),
      );

    await expect(bookNativeAppointment(bookInput())).rejects.toMatchObject({
      code: "validation",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("cancels through the cancel endpoint and acknowledges the cancel kind", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        success("cancelled", { ...DETAIL, status: "Cancelled" }),
      )
      .mockResolvedValueOnce(acknowledged());

    await expect(cancelNativeAppointment(cancelInput())).resolves.toEqual({
      status: "cancelled",
      appointment: { ...DETAIL, status: "Cancelled" },
    });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments_cancel_write.cancel",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      patient: "WS-1",
      appointment_id: ALIAS,
      reason: "Patient called to cancel.",
    });
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments.ack",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      kind: "appointment_cancel",
      ack_token: TOKEN,
    });
    expect(appointmentWriteLocked()).toBe(false);
  });

  it("records a no-show through its own endpoint and kind", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("no_show", { ...DETAIL, status: "No Show" }))
      .mockResolvedValueOnce(acknowledged());

    await expect(
      noShowNativeAppointment(cancelInput()),
    ).resolves.toMatchObject({ status: "no_show" });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.appointments_cancel_write.no_show",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      kind: "appointment_no_show",
      ack_token: TOKEN,
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
    expect(appointmentWriteLocked()).toBe(false);
  });

  it("keeps the lock and never retries when the cancel acknowledgement is lost", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("cancelled", { ...DETAIL, status: "Cancelled" }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: { acknowledged: false } }), {
          status: 200,
        }),
      );

    await expect(cancelNativeAppointment(cancelInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(appointmentWriteLocked()).toBe(true);
    await expect(cancelNativeAppointment(cancelInput())).rejects.toMatchObject({
      code: "locked",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the lock after a cancel response that names the wrong status", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(success("cancelled", DETAIL));

    await expect(cancelNativeAppointment(cancelInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(appointmentWriteLocked()).toBe(true);
  });
});
