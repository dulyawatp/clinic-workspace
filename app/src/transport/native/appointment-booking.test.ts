import { afterEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { createDepartmentAppointment, createPractitionerAppointment, listPractitionerSlots, searchBookingLinks } from "./appointment-booking";

vi.mock("./session", () => ({ nativeCsrfToken: () => "token" }));

const context = { patient: "PAT-1", company: "Clinic", department: "Cardiology", appointment_type: "Consult" };
const input = { practitioner: "DOC-1", date: "2099-01-01", appointment: context };
const method = "http://localhost:5173/api/method/healthcare.healthcare.doctype.patient_appointment.patient_appointment.get_availability_data";

afterEach(() => server.resetHandlers());

describe("native practitioner booking", () => {
  it("enriches only practitioner IDs without native human labels using one bounded resource list", async () => {
    let resourceUrl: URL | undefined;
    server.use(
      http.get("http://localhost:5173/api/method/frappe.desk.search.search_link", () => HttpResponse.json({ message: [
        { value: "DOC-1", label: "DOC-1", description: "ignored, 555" },
        { value: "DOC-2" },
        { value: "DOC-3", label: "Dr Native" },
      ] })),
      http.get("http://localhost:5173/api/resource/Healthcare%20Practitioner", ({ request }) => {
        resourceUrl = new URL(request.url);
        return HttpResponse.json({ data: [{ name: "DOC-1", practitioner_name: "Dr One" }, { name: "DOC-3", practitioner_name: "Wrong" }] });
      }),
    );
    const options = await searchBookingLinks("practitioner", "doctor", new AbortController().signal, { department: "Cardiology" });
    expect(options).toEqual([{ id: "DOC-1", label: "Dr One" }, { id: "DOC-2", label: "DOC-2" }, { id: "DOC-3", label: "Dr Native" }]);
    expect(JSON.parse(resourceUrl!.searchParams.get("fields")!)).toEqual(["name", "practitioner_name"]);
    expect(JSON.parse(resourceUrl!.searchParams.get("filters")!)).toEqual([["name", "in", ["DOC-1", "DOC-2"]]]);
    expect(resourceUrl!.searchParams.get("limit_page_length")).toBe("10");
  });

  it("keeps native IDs if enrichment is denied and skips enrichment for native human labels", async () => {
    let requests = 0;
    let nativeLabel = false;
    server.use(
      http.get("http://localhost:5173/api/method/frappe.desk.search.search_link", () => HttpResponse.json({ message: [{ value: "DOC-1", label: nativeLabel ? "Dr Native" : "DOC-1" }] })),
      http.get("http://localhost:5173/api/resource/Healthcare%20Practitioner", () => { requests++; return new HttpResponse(null, { status: 403 }); }),
    );
    const signal = new AbortController().signal;
    expect(await searchBookingLinks("practitioner", "", signal)).toEqual([{ id: "DOC-1", label: "DOC-1" }]);
    nativeLabel = true;
    expect(await searchBookingLinks("practitioner", "", signal)).toEqual([{ id: "DOC-1", label: "Dr Native" }]);
    expect(requests).toBe(1);
  });

  it("propagates cancellation during the practitioner resource lookup", async () => {
    const controller = new AbortController();
    server.use(
      http.get("http://localhost:5173/api/method/frappe.desk.search.search_link", () => HttpResponse.json({ message: [{ value: "DOC-1" }] })),
      http.get("http://localhost:5173/api/resource/Healthcare%20Practitioner", () => {
        controller.abort();
        return HttpResponse.error();
      }),
    );
    await expect(searchBookingLinks("practitioner", "", controller.signal)).rejects.toBeDefined();
  });

  it("uses Desk link filters for practitioner and appointment type", async () => {
    const urls: URL[] = [];
    server.use(http.get("http://localhost:5173/api/method/frappe.desk.search.search_link", ({ request }) => {
      urls.push(new URL(request.url));
      return HttpResponse.json({ message: [] });
    }));
    const signal = new AbortController().signal;
    await searchBookingLinks("practitioner", "", signal, { department: "Cardiology" });
    await searchBookingLinks("appointment_type", "", signal, { appointment_for: "Practitioner" });
    expect(urls).toHaveLength(2);
    expect(urls[0]!.searchParams.get("doctype")).toBe("Healthcare Practitioner");
    expect(urls[0]!.searchParams.has("ignore_user_permissions")).toBe(false);
    expect(JSON.parse(urls[0]!.searchParams.get("filters")!)).toEqual({ department: "Cardiology" });
    expect(JSON.parse(urls[1]!.searchParams.get("filters")!)).toEqual({ allow_booking_for: "Practitioner" });
  });

  it("sends appointment context and returns only bookable slots without booked details", async () => {
    let requestBody: unknown;
    server.use(http.post(method, async ({ request }) => {
      requestBody = await request.json();
      return HttpResponse.json({ message: { slot_details: [
        { service_unit: "Room", allow_overlap: 0, tele_conf: 1, avail_slot: [
          { from_time: "09:00:00", to_time: "09:30:00" }, { from_time: "09:30:00", to_time: "10:00:00" },
        ], appointments: [{ name: "PRIVATE", appointment_time: "09:00:00", duration: 30 }] },
      ] } });
    }));
    const slots = await listPractitionerSlots(input);
    expect(requestBody).toEqual({ practitioner: "DOC-1", date: "2099-01-01", appointment: { doctype: "Patient Appointment", __islocal: 1, appointment_for: "Practitioner", ...context } });
    expect(slots).toEqual([{ appointment_time: "09:30:00", end_time: "10:00:00", duration: 30, service_unit: "Room", appointment_based_on_check_in: 0, add_video_conferencing: 1 }]);
    expect(JSON.stringify(slots)).not.toContain("PRIVATE");
  });

  it("compares single-digit native hours and preserves the selected time on insert", async () => {
    let saved: Record<string, unknown> = {};
    server.use(
      http.post(method, () => HttpResponse.json({ message: { slot_details: [{
        allow_overlap: 0, avail_slot: [
          { from_time: "9:00:00", to_time: "9:30:00" },
          { from_time: "9:30:00", to_time: "10:00:00" },
        ], appointments: [{ appointment_time: "9:00:00", duration: 30 }],
      }] } })),
      http.post("http://localhost:5173/api/resource/Patient%20Appointment", async ({ request }) => {
        saved = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ data: { ...saved, doctype: "Patient Appointment", name: "APT-1", status: "Scheduled" } });
      }),
    );
    const slots = await listPractitionerSlots(input);
    expect(slots).toEqual([{ appointment_time: "9:30:00", end_time: "10:00:00", duration: 30, service_unit: "", appointment_based_on_check_in: 0, add_video_conferencing: 0 }]);
    await createPractitionerAppointment({ ...context, practitioner: input.practitioner, appointment_date: input.date, slot: slots[0]! });
    expect(saved.appointment_time).toBe("9:30:00");
  });

  it("matches Desk capacity, unavailable and day appointment handling", async () => {
    server.use(http.post(method, () => HttpResponse.json({ message: { slot_details: [
      { allow_overlap: 1, service_unit_capacity: 2, tele_conf: 1, avail_slot: [
        { from_time: "09:00:00", to_time: "09:30:00" }, { from_time: "10:00:00", to_time: "10:30:00" },
      ], appointments: [
        { appointment_time: "09:00:00", duration: 30 }, { appointment_time: "10:00:00", duration: 30, type: "Unavailable" },
      ] },
      { allow_overlap: 0, avail_slot: [{ from_time: "08:00:00", to_time: "17:00:00", duration: 12, maximum_appointments: 2 }], appointments: [
        { appointment_time: "09:00:00", duration: 30, appointment_date: "2099-01-01" },
      ] },
    ] } })));
    expect(await listPractitionerSlots(input)).toEqual([
      { appointment_time: "09:00:00", end_time: "09:30:00", duration: 30, service_unit: "", appointment_based_on_check_in: 0, add_video_conferencing: 0 },
      { appointment_time: "08:00:00", end_time: "17:00:00", duration: 12, service_unit: "", appointment_based_on_check_in: 1, add_video_conferencing: 0 },
    ]);
  });

  it("saves selected native metadata and propagates native validation errors", async () => {
    let body: unknown;
    server.use(http.post("http://localhost:5173/api/resource/Patient%20Appointment", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ exc_type: "OverlapError" }, { status: 417 });
    }));
    const booking = { ...context, practitioner: "DOC-1", appointment_date: "2099-01-01", slot: {
      appointment_time: "09:30:00", end_time: "10:00:00", duration: 30, service_unit: "Room", appointment_based_on_check_in: 0 as const, add_video_conferencing: 1 as const,
    } };
    await expect(createPractitionerAppointment(booking)).rejects.toMatchObject({ reason: "validation" });
    expect(body).toEqual({ ...context, practitioner: "DOC-1", appointment_date: "2099-01-01", appointment_for: "Practitioner", appointment_time: "09:30:00", duration: 30, service_unit: "Room", appointment_based_on_check_in: 0, add_video_conferencing: 1 });
  });

  it("keeps Department insert unchanged and accepts confirmed native responses", async () => {
    let body: Record<string, unknown> = {};
    server.use(http.post("http://localhost:5173/api/resource/Patient%20Appointment", async ({ request }) => {
      body = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ data: { ...body, doctype: "Patient Appointment", name: "APT-1", status: "Scheduled" } });
    }));
    const department = { ...context, appointment_date: "2099-01-01", appointment_time: "10:00:00" };
    expect(await createDepartmentAppointment(department)).toEqual({ id: "APT-1", status: "Scheduled" });
    expect(body).toEqual({ ...department, appointment_for: "Department" });
    const practitioner = { ...context, practitioner: "DOC-1", appointment_date: "2099-01-01", slot: { appointment_time: "10:00:00", end_time: "10:30:00", duration: 30, service_unit: "", appointment_based_on_check_in: 0 as const, add_video_conferencing: 0 as const } };
    expect(await createPractitionerAppointment(practitioner)).toEqual({ id: "APT-1", status: "Scheduled" });
    await createPractitionerAppointment({ ...practitioner, duration: 45 });
    expect(body.duration).toBe(45);
  });
});
