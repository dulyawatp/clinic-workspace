import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { server } from "../../test/msw/server";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AppointmentBooking } from "./AppointmentBooking";

const LINK = "/api/method/frappe.desk.search.search_link";
const AVAILABILITY = "/api/method/healthcare.healthcare.doctype.patient_appointment.patient_appointment.get_availability_data";
const BOOK = "/api/resource/Patient%20Appointment";
const DATE = "2099-11-25";
const NEXT_DATE = "2099-11-26";

function availability(start = "10:00:00", end = "10:20:00") {
  return HttpResponse.json({
    message: { slot_details: [{
      avail_slot: [{ from_time: "09:00:00", to_time: "09:20:00" }, { from_time: start, to_time: end }],
      appointments: [{
        appointment_time: "09:00:00", duration: 20, appointment_date: DATE, patient: "PRIVATE-PATIENT",
      }],
      service_unit: "Room One",
    }] },
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({
      message: {
        mode: "live",
        sessionEpoch: "booking-test",
        user: { id: "native|u1", displayName: "Native Receptionist", roles: ["REC"] },
        capabilities: { "write.appointment.book": "enabled" },
        csrfToken: "csrf",
      },
    })),
    http.post(LINK, () => HttpResponse.json({ message: [{ value: "PAT-1" }, { value: "PAT-2" }] })),
    http.get(LINK, ({ request }) => {
      const params = new URL(request.url).searchParams;
      const options = {
        company: ["Hospital Co"],
        department: ["General Medicine"],
        practitioner: ["Doctor One", "Doctor Two"],
        appointment_type: [
          params.get("filters")?.includes("Practitioner") ? "Doctor Visit" : "Department Visit",
        ],
      }[params.get("link_fieldname") ?? ""] ?? [];
      return HttpResponse.json({ message: options.map((value) => ({ value })) });
    }),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

async function fillBooking(practitioner: boolean) {
  const user = userEvent.setup();
  renderWithFixtures(<AppointmentBooking />, { path: "/clinic/reception?book=1" });
  if (practitioner) {
    await user.selectOptions(await screen.findByRole("combobox", { name: "จองตาม" }), "Practitioner");
  }
  const links: [string, string][] = [
    ["ผู้ป่วย", "PAT-1"],
    ["บริษัท", "Hospital Co"],
    practitioner ? ["แพทย์", "Doctor One"] : ["แผนก", "General Medicine"],
    ["ประเภทนัดหมาย", practitioner ? "Doctor Visit" : "Department Visit"],
  ];
  for (const [label, id] of links) {
    await screen.findByRole("option", { name: id });
    await user.selectOptions(screen.getByRole("combobox", { name: label }), id);
  }
  fireEvent.change(screen.getByLabelText("วันที่"), { target: { value: DATE } });
  return user;
}

const confirm = () => screen.getByRole("button", { name: "ยืนยันการจอง" });
const times = () => screen.getByRole("combobox", { name: "เวลา" });

it("shows only available times and books one native appointment with the chosen doctor and slot", async () => {
  const lookup = vi.fn();
  const book = vi.fn();
  server.use(
    http.post(AVAILABILITY, async ({ request }) => {
      lookup(await request.json());
      return availability();
    }),
    http.post(BOOK, async ({ request }) => {
      const payload = await request.json() as Record<string, unknown>;
      book(payload);
      return HttpResponse.json({
        data: { ...payload, name: "HLC-APP-0001", status: "Scheduled", doctype: "Patient Appointment" },
      });
    }),
  );
  const user = await fillBooking(true);
  const open = await screen.findByRole("option", { name: /10:00.*10:20/ });
  expect(screen.queryByRole("option", { name: /09:00/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/PRIVATE-PATIENT/)).not.toBeInTheDocument();
  expect(lookup).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    practitioner: "Doctor One",
    date: DATE,
    appointment: expect.objectContaining({ appointment_type: "Doctor Visit", patient: "PAT-1", company: "Hospital Co" }),
  }));
  expect(confirm()).toBeDisabled();
  await user.selectOptions(times(), open);
  await user.click(confirm());
  expect(await screen.findByRole("link", { name: "HLC-APP-0001" })).toBeVisible();
  expect(book).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    practitioner: "Doctor One", appointment_for: "Practitioner", appointment_date: DATE,
    appointment_time: "10:00:00", service_unit: "Room One", duration: 20,
  }));
});

it("clears selected times after date, doctor, or appointment context changes", async () => {
  server.use(http.post(AVAILABILITY, async ({ request }) => {
    const { date, practitioner, appointment } = await request.json() as {
      date: string; practitioner: string; appointment: { patient: string };
    };
    if (appointment.patient === "PAT-2") return availability("13:00:00", "13:20:00");
    if (practitioner === "Doctor Two") return availability("12:00:00", "12:20:00");
    if (date === NEXT_DATE) return availability("11:00:00", "11:20:00");
    return availability();
  }));
  const user = await fillBooking(true);
  await user.selectOptions(times(), await screen.findByRole("option", { name: /10:00.*10:20/ }));
  expect(confirm()).toBeEnabled();

  fireEvent.change(screen.getByLabelText("วันที่"), { target: { value: NEXT_DATE } });
  expect(screen.queryByRole("option", { name: /10:00.*10:20/ })).not.toBeInTheDocument();
  expect(confirm()).toBeDisabled();
  await user.selectOptions(times(), await screen.findByRole("option", { name: /11:00.*11:20/ }));
  await user.selectOptions(screen.getByRole("combobox", { name: "แพทย์" }), "Doctor Two");
  expect(screen.queryByRole("option", { name: /11:00.*11:20/ })).not.toBeInTheDocument();
  expect(confirm()).toBeDisabled();
  await user.selectOptions(times(), await screen.findByRole("option", { name: /12:00.*12:20/ }));
  await user.selectOptions(screen.getByRole("combobox", { name: "ผู้ป่วย" }), "PAT-2");
  expect(screen.queryByRole("option", { name: /12:00.*12:20/ })).not.toBeInTheDocument();
  expect(await screen.findByRole("option", { name: /13:00.*13:20/ })).toBeInTheDocument();
  expect(times()).toHaveValue("");
  expect(confirm()).toBeDisabled();
});

it("distinguishes an empty schedule from a failed availability lookup", async () => {
  server.use(http.post(AVAILABILITY, async ({ request }) => {
    const { date } = await request.json() as { date: string };
    return date === DATE ? HttpResponse.json({ message: { slot_details: [] } }) :
      new HttpResponse(null, { status: 503 });
  }));
  await fillBooking(true);
  expect(await screen.findByText("ไม่พบเวลาว่างในวันที่เลือก")).toBeVisible();
  fireEvent.change(screen.getByLabelText("วันที่"), { target: { value: NEXT_DATE } });
  expect(await screen.findByRole("alert")).toHaveTextContent("โหลดเวลาว่างไม่สำเร็จ");
  expect(screen.queryByText("ไม่พบเวลาว่างในวันที่เลือก")).not.toBeInTheDocument();
  expect(confirm()).toBeDisabled();
});

it("cancels a pending lookup when the selected date changes", async () => {
  const firstLookup = vi.fn();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  server.use(http.post(AVAILABILITY, async ({ request }) => {
    const { date } = await request.json() as { date: string };
    if (date === DATE) {
      firstLookup(request.signal);
      await pending;
      return availability();
    }
    return availability("11:00:00", "11:20:00");
  }));
  try {
    await fillBooking(true);
    await waitFor(() => expect(firstLookup).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("วันที่"), { target: { value: NEXT_DATE } });
    expect(await screen.findByRole("option", { name: /11:00.*11:20/ })).toBeVisible();
    expect(firstLookup.mock.calls[0]?.[0].aborted).toBe(true);
    expect(screen.queryByRole("option", { name: /10:00.*10:20/ })).not.toBeInTheDocument();
  } finally {
    release();
  }
});

it("shows the native refusal without linking an appointment or sending a second write", async () => {
  const book = vi.fn();
  server.use(
    http.post(AVAILABILITY, () => availability()),
    http.post(BOOK, () => {
      book();
      return HttpResponse.json({
        exc_type: "OverlapError",
        _server_messages: JSON.stringify([JSON.stringify({
          raise_exception: 1, message: "Time is no longer available.",
        })]),
      }, { status: 417 });
    }),
  );
  const user = await fillBooking(true);
  await user.selectOptions(times(), await screen.findByRole("option", { name: /10:00.*10:20/ }));
  await user.click(confirm());
  expect(await screen.findByRole("alert")).toHaveTextContent("Time is no longer available.");
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(book).toHaveBeenCalledTimes(1);
});

it("keeps Department booking on manual time without querying doctor availability", async () => {
  const lookup = vi.fn();
  const book = vi.fn();
  server.use(
    http.post(AVAILABILITY, () => { lookup(); return availability(); }),
    http.post(BOOK, async ({ request }) => {
      const payload = await request.json() as Record<string, unknown>;
      book(payload);
      return HttpResponse.json({
        data: { ...payload, name: "HLC-APP-0002", status: "Scheduled", doctype: "Patient Appointment" },
      });
    }),
  );
  const user = await fillBooking(false);
  expect(screen.queryByRole("combobox", { name: "แพทย์" })).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("เวลา"), "10:15");
  await user.click(confirm());
  await waitFor(() => expect(book).toHaveBeenCalledExactlyOnceWith({
    appointment_for: "Department", patient: "PAT-1", company: "Hospital Co", department: "General Medicine",
    appointment_type: "Department Visit", appointment_date: DATE, appointment_time: "10:15",
  }));
  expect(await screen.findByRole("link", { name: "HLC-APP-0002" })).toBeVisible();
  expect(lookup).not.toHaveBeenCalled();
});
