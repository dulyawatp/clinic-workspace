import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import { server } from "../../test/msw/server";
import { AddDoctor } from "./AddDoctor";

vi.mock("../../transport/native/session", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../transport/native/session")>(),
  nativeCsrfToken: () => "test-csrf",
}));

const api = "http://localhost:5173/api/method/clinic_ui.api";
const finishApi = `${api}.owner_doctor_finish`;
const get = screen.getByTestId;
const find = screen.findByTestId;
const query = screen.queryByTestId;
const practitioner = "HP-01";
const schedule = "Existing schedule";
const unit = "Appointment room";
const version = "review-1";
const digest = "a".repeat(64);
const nativeSlots = [
  { day: "Tuesday", from_time: "09:00:17", to_time: "12:00:00", duration: 20, maximum_appointments: 10 },
  { day: "Monday", from_time: "13:00:00", to_time: "17:00:00", duration: 15, maximum_appointments: 0 },
];
const review = {
  practitioner, practitionerVersion: version, schedule, scheduleVersion: version,
  serviceUnit: unit, firstName: "Ari", lastName: "Test", slotDigest: digest,
};
const available = {
  practitioner, practitionerVersion: version,
  schedules: [{ name: schedule }], serviceUnits: [{ name: unit }],
};

async function pickSchedule() {
  fireEvent.click(await find("owner-doctor-reuse-start"));
  const scheduleInput = get("owner-doctor-reuse-schedule");
  const unitInput = get("owner-doctor-reuse-unit");
  const reviewButton = get("owner-doctor-reuse-review");
  expect(scheduleInput).toHaveFocus();
  expect(scheduleInput).toHaveValue("");
  expect(unitInput).toHaveValue("");
  expect(reviewButton).toBeDisabled();
  expect(query("owner-doctor-finish-form")).not.toBeInTheDocument();
  fireEvent.change(scheduleInput, { target: { value: schedule } });
  expect(reviewButton).toBeDisabled();
  fireEvent.change(unitInput, { target: { value: unit } });
  return reviewButton;
}

async function openDoctor() {
  server.use(
    http.get(`${api}.owner_doctor_profile.lookup`, () => HttpResponse.json({
      message: { departments: [], practitioners: [{ name: practitioner, displayName: "Ari Test", version }] },
    })),
    http.get(`${api}.owner_doctor_hours.lookup`, () => new HttpResponse(null, { status: 404 })),
  );
  const onFinished = vi.fn();
  render(<LocaleProvider initial="en"><AddDoctor onFinished={onFinished} /></LocaleProvider>);
  fireEvent.click(await find("owner-doctor-start"));
  fireEvent.change(get("owner-doctor-existing"), { target: { value: practitioner } });
  fireEvent.click(get("owner-doctor-continue"));
  return onFinished;
}

it("requires both deliberate choices and reviews preserved native hours before sending reuse Finish", async () => {
  const read = vi.fn();
  const write = vi.fn();
  server.use(
    http.get(`${finishApi}.options`, () => new HttpResponse(null, { status: 404 })),
    http.get(`${finishApi}.reuse_options`, ({ request }) => {
      expect(new URL(request.url).searchParams.get("practitioner")).toBe(practitioner);
      return HttpResponse.json({ message: available });
    }),
    http.get(`${finishApi}.reuse_review`, ({ request }) => {
      read(Object.fromEntries(new URL(request.url).searchParams));
      const renewed = read.mock.calls.length > 1;
      return HttpResponse.json({
        message: { ...review, mode: "reuse", slots: nativeSlots, ...(renewed && { slotDigest: "b".repeat(64) }) },
      });
    }),
    http.post(`${finishApi}.finish`, async ({ request }) => {
      write(await request.json());
      return write.mock.calls.length === 1
        ? HttpResponse.json({ message: { code: "STALE_VERSION" } }, { status: 409 })
        : HttpResponse.json({
          message: { practitioner, userId: "ari@example.test", schedule, serviceUnit: unit, slots: nativeSlots, enabled: true },
        });
    }),
  );
  const onFinished = await openDoctor();
  const reviewButton = await pickSchedule();
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(reviewButton);
  expect(await find("owner-doctor-reuse-warning")).toHaveTextContent(/later edits will affect every doctor/i);
  const hours = get("owner-doctor-reuse-hours");
  expect(hours).toHaveTextContent("09:00:17");
  expect(hours.textContent?.indexOf("09:00:17")).toBeLessThan(hours.textContent?.indexOf("13:00:00") ?? 0);
  expect(query("owner-doctor-reviewed-hours")).not.toBeInTheDocument();
  expect(read).toHaveBeenCalledExactlyOnceWith({ practitioner, schedule, service_unit: unit });
  expect(write).not.toHaveBeenCalled();
  fireEvent.change(get("owner-doctor-finish-email"), { target: { value: "ari@example.test" } });
  fireEvent.click(get("owner-doctor-finish"));
  expect(await find("owner-doctor-finish-stale")).toBeVisible();
  expect(query("owner-doctor-finish")).not.toBeInTheDocument();
  expect(write).toHaveBeenCalledExactlyOnceWith({
    practitioner, practitionerVersion: version, schedule, scheduleVersion: version,
    slotDigest: digest, email: "ari@example.test", mode: "reuse", serviceUnit: unit,
  });
  fireEvent.click(get("owner-doctor-close"));
  fireEvent.click(await find("owner-doctor-start"));
  await waitFor(() => expect(get("owner-doctor-existing")).toBeEnabled());
  fireEvent.change(get("owner-doctor-existing"), { target: { value: practitioner } });
  fireEvent.click(get("owner-doctor-continue"));
  fireEvent.click(await find("owner-doctor-finish-check"));
  expect(await find("owner-doctor-reuse-warning")).toBeVisible();
  expect(read).toHaveBeenLastCalledWith({ practitioner, schedule, service_unit: unit });
  expect(read).toHaveBeenCalledTimes(2);
  expect(write).toHaveBeenCalledTimes(1);
  fireEvent.change(get("owner-doctor-finish-email"), { target: { value: "ari@example.test" } });
  fireEvent.click(get("owner-doctor-finish"));
  expect(await find("owner-doctor-finish-receipt")).toBeVisible();
  expect(write).toHaveBeenLastCalledWith(expect.objectContaining({ slotDigest: "b".repeat(64), mode: "reuse" }));
  expect(onFinished).toHaveBeenCalledTimes(1);
});

it("waits for verified choices and blocks a review for the wrong service unit", async () => {
  const choiceRequest = vi.fn();
  let resolve!: () => void;
  const choiceGate = new Promise<void>((release) => { resolve = release; });
  server.use(
    http.get(`${finishApi}.options`, () => new HttpResponse(null, { status: 404 })),
    http.get(`${finishApi}.reuse_options`, async () => {
      choiceRequest();
      await choiceGate;
      return HttpResponse.json({ message: available });
    }),
    http.get(`${finishApi}.reuse_review`, () => HttpResponse.json({
      message: { ...review, mode: "reuse", serviceUnit: "Different unit", slots: nativeSlots },
    })),
  );
  await openDoctor();
  await waitFor(() => expect(choiceRequest).toHaveBeenCalledTimes(1));
  expect(query("owner-doctor-reuse-start")).not.toBeInTheDocument();
  resolve();
  fireEvent.click(await pickSchedule());
  expect(await find("owner-doctor-reuse-read-error")).toBeVisible();
  expect(query("owner-doctor-finish-form")).not.toBeInTheDocument();
  fireEvent.click(get("owner-doctor-reuse-cancel"));
  expect(get("owner-doctor-reuse-start")).toHaveFocus();
  expect(await find("owner-hours-error")).toBeVisible();
});

it("keeps uncertain reuse Finish locked across close and reopen even when schedule review is available", async () => {
  const read = vi.fn();
  const write = vi.fn();
  const privateReview = vi.fn();
  server.use(
    http.get(`${finishApi}.options`, () => {
      if (!write.mock.calls.length) return new HttpResponse(null, { status: 404 });
      privateReview();
      return HttpResponse.json({ message: {
        ...review, slots: [{ day: "Monday", from_time: "09:00", to_time: "12:00", duration: 15, maximum_appointments: 1 }],
      } });
    }),
    http.get(`${finishApi}.reuse_options`, () => HttpResponse.json({ message: available })),
    http.get(`${finishApi}.reuse_review`, () => {
      read();
      return HttpResponse.json({ message: { ...review, mode: "reuse", slots: nativeSlots } });
    }),
    http.post(`${finishApi}.finish`, () => {
      write();
      return HttpResponse.error();
    }),
  );
  const onFinished = await openDoctor();
  fireEvent.click(await pickSchedule());
  expect(await find("owner-doctor-reuse-warning")).toBeVisible();
  fireEvent.change(get("owner-doctor-finish-email"), { target: { value: "ari@example.test" } });
  fireEvent.click(get("owner-doctor-finish"));
  async function expectLocked() {
    expect(await find("owner-doctor-finish-uncertain")).toBeVisible();
    expect(query("owner-doctor-finish-check")).not.toBeInTheDocument();
    expect(query("owner-doctor-finish-form")).not.toBeInTheDocument();
    expect(query("owner-doctor-reuse-change")).not.toBeInTheDocument();
    expect(query("owner-doctor-reuse-start")).not.toBeInTheDocument();
    expect(get("owner-doctor-finish-uncertain")).toHaveTextContent(/doctor in the directory.*current login account/i);
  }
  await expectLocked();
  fireEvent.click(get("owner-doctor-close"));
  fireEvent.click(await find("owner-doctor-start"));
  await waitFor(() => expect(get("owner-doctor-existing")).toBeEnabled());
  fireEvent.change(get("owner-doctor-existing"), { target: { value: practitioner } });
  fireEvent.click(get("owner-doctor-continue"));
  await expectLocked();
  expect(read).toHaveBeenCalledTimes(1);
  expect(privateReview).not.toHaveBeenCalled();
  expect(write).toHaveBeenCalledTimes(1);
  expect(onFinished).not.toHaveBeenCalled();
});

it("keeps the private review and payload unchanged when opening a saved private draft", async () => {
  const write = vi.fn();
  const reuseOptions = vi.fn();
  server.use(
    http.get(`${finishApi}.options`, () => HttpResponse.json({
      message: { ...review, slots: [{ day: "Monday", from_time: "09:00", to_time: "12:00", duration: 15, maximum_appointments: 1 }] },
    })),
    http.get(`${finishApi}.reuse_options`, () => {
      reuseOptions();
      return HttpResponse.json({ message: available });
    }),
    http.post(`${finishApi}.finish`, async ({ request }) => {
      write(await request.json());
      return new HttpResponse(null, { status: 422 });
    }),
  );
  await openDoctor();
  expect(await find("owner-doctor-reviewed-hours")).toBeVisible();
  expect(query("owner-doctor-reuse-start")).not.toBeInTheDocument();
  expect(reuseOptions).not.toHaveBeenCalled();
  expect(query("owner-doctor-reuse-warning")).not.toBeInTheDocument();
  fireEvent.change(get("owner-doctor-finish-email"), { target: { value: "ari@example.test" } });
  fireEvent.click(get("owner-doctor-finish"));
  expect(await find("owner-doctor-finish-validation")).toBeVisible();
  expect(write).toHaveBeenCalledExactlyOnceWith({
    practitioner, practitionerVersion: version, schedule, scheduleVersion: version,
    slotDigest: digest, email: "ari@example.test",
  });
});
