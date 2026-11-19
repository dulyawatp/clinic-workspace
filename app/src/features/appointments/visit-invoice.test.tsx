import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AppointmentPage } from "./AppointmentPage";

const base = "/api/method/clinic_ui.api.visit_invoice";
const alias = "clinic-alias";
const appointment = "native-appointment";
const send = { "write.visit_invoice.submit": "enabled" };
const discount = { "write.visit_invoice.discount": "enabled" };
const preview = (token: string, cut = "12.50", total = "87.50") =>
  HttpResponse.json({ message: { currency: "USD", gross: "100.00", discount: cut, total, preview_token: token } });

function page() {
  renderWithFixtures(
    <Routes><Route path="/appointments/:id" element={<AppointmentPage />} /></Routes>,
    { path: `/appointments/${alias}`, role: "DOC" },
  );
}

function native(caps: Record<string, string>, role = "DOC", hasExactId = true) {
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
      HttpResponse.json({ message: {
        mode: "live", sessionEpoch: "native", user: { id: "u1", displayName: "User", roles: [role] },
        capabilities: caps, csrfToken: "csrf",
      } }),
    ),
    http.get("/api/method/clinic_ui.api.appointments.view", () =>
      HttpResponse.json({ message: {
        appointment_id: alias, workspace_patient_id: "patient", appointment_date: "2026-11-20",
        appointment_time: "09:00:00", status: "Open", modified: "2026-11-20 08:00:00",
        can_reschedule: false, can_checkin: false,
        ...(hasExactId ? { visit_invoice_appointment_id: appointment } : {}),
      } }),
    ),
  );
}

const previewName = "ตรวจสอบยอดใบแจ้งหนี้";
const confirmName = "ยืนยันออกใบแจ้งหนี้";
const lookupName = "ค้นหาใบแจ้งหนี้ของนัดหมาย";
const title = "ใบแจ้งหนี้สำหรับนัดหมาย";

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

it.each([
  ["different native role", "REC", true],
  ["doctor without exact appointment ID", "DOC", false],
] as const)("does not expose invoice actions for %s", async (_, role, hasExactId) => {
  native({ ...send, ...discount }, role, hasExactId);
  page();
  expect(await screen.findByTestId("appointment-reference")).toHaveTextContent(alias);
  expect(screen.queryByRole("region", { name: title })).not.toBeInTheDocument();
});

it("does not expose doctor invoice controls in fixture mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "0");
  renderWithFixtures(
    <Routes><Route path="/appointments/:id" element={<AppointmentPage />} /></Routes>,
    { path: "/appointments/APT-0001", role: "DOC" },
  );
  await screen.findByRole("heading", { name: "รายละเอียดนัดหมาย" });
  expect(screen.queryByRole("region", { name: title })).not.toBeInTheDocument();
});

it("allows only a permission-filtered read when invoice submission is off", async () => {
  native(discount);
  const lookup = vi.fn();
  server.use(http.get(`${base}.view`, ({ request }) => {
    lookup(new URL(request.url).searchParams.get("appointment_id"));
    return HttpResponse.json({ message: { invoice_id: "INV-POSTED" } });
  }));
  page();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: lookupName }));
  expect(await screen.findByText(/INV-POSTED/)).toBeVisible();
  expect(lookup).toHaveBeenCalledExactlyOnceWith(appointment);
  expect(screen.queryByRole("button", { name: previewName })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

it("previews an undiscounted invoice when only the submit capability is enabled", async () => {
  native(send);
  const seen = vi.fn();
  server.use(http.post(`${base}.preview`, async ({ request }) => {
    seen(await request.json());
    return preview("none", "0.00", "100.00");
  }));
  page();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: previewName }));
  expect(await screen.findByText("USD 0.00")).toBeVisible();
  expect(seen).toHaveBeenCalledExactlyOnceWith({ appointment_id: appointment });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

it("reviews one discount mode at a time, invalidates edited previews, and confirms once", async () => {
  native({ ...send, ...discount });
  const previews = vi.fn();
  const submits = vi.fn();
  let finish: (() => void) | undefined;
  const inFlight = new Promise<void>((resolve) => { finish = resolve; });
  server.use(
    http.post(`${base}.preview`, async ({ request }) => {
      const body = await request.json();
      previews(body);
      return previews.mock.calls.length === 1 ? preview("amount") : preview("percent", "15.00", "85.00");
    }),
    http.post(`${base}.submit`, async ({ request }) => {
      submits(await request.json());
      await inFlight;
      return HttpResponse.json({ message: { invoice_id: "INV-OK" } });
    }),
  );
  page();
  const user = userEvent.setup();
  const region = within(await screen.findByRole("region", { name: title }));
  const mode = region.getByRole("combobox");
  expect(region.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
  await user.selectOptions(mode, "amount");
  await user.type(region.getByRole("textbox"), "12.50");
  await user.click(region.getByRole("button", { name: previewName }));
  expect(await region.findByText("USD 87.50")).toBeVisible();
  expect(region.getByText("USD 100.00")).toBeVisible();
  expect(region.getByText("USD 12.50")).toBeVisible();
  await user.selectOptions(mode, "percent");
  expect(region.queryByText("USD 87.50")).not.toBeInTheDocument();
  expect(region.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
  await user.type(region.getByRole("textbox"), "15");
  await user.click(region.getByRole("button", { name: previewName }));
  expect(await region.findByText("USD 85.00")).toBeVisible();
  const confirm = region.getByRole("button", { name: confirmName });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(confirm).toBeDisabled();
  expect(region.getByRole("button", { name: lookupName })).toBeDisabled();
  finish?.();
  expect(await region.findByText(/INV-OK/)).toBeVisible();
  expect(previews.mock.calls).toEqual([
    [{ appointment_id: appointment, discount: { mode: "amount", value: "12.50" } }],
    [{ appointment_id: appointment, discount: { mode: "percent", value: "15" } }],
  ]);
  expect(submits).toHaveBeenCalledExactlyOnceWith({
    appointment_id: appointment, discount: { mode: "percent", value: "15" }, preview_token: "percent",
  });
});

it("removes confirmation and reports normalized validation without claiming uncertainty", async () => {
  native(send);
  server.use(
    http.post(`${base}.preview`, () => preview("none", "0.00", "100.00")),
    http.post(`${base}.submit`, () => HttpResponse.json({ code: "VALIDATION" }, { status: 417 })),
  );
  page();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: previewName }));
  await user.click(await screen.findByRole("button", { name: confirmName }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ข้อมูลใบแจ้งหนี้ไม่ถูกต้อง");
  expect(screen.getByRole("button", { name: previewName })).toBeEnabled();
  expect(screen.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
});

it("locks an uncertain visit after a denied lookup, then shows the posted reference when readable", async () => {
  native(send);
  let canView = false;
  const submit = vi.fn();
  server.use(
    http.post(`${base}.preview`, () => preview("none", "0.00", "100.00")),
    http.post(`${base}.submit`, () => {
      submit();
      return HttpResponse.json({ message: { invoice_id: 1 } });
    }),
    http.get(`${base}.view`, () => canView
      ? HttpResponse.json({ message: { invoice_id: "INV-FOUND" } })
      : HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 })),
  );
  page();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: previewName }));
  await user.click(await screen.findByRole("button", { name: confirmName }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ยังยืนยันผลการออกใบแจ้งหนี้ไม่ได้");
  await user.click(screen.getByRole("button", { name: lookupName }));
  expect(await screen.findByText(/ไม่ได้ยืนยันว่าไม่มีการออกใบแจ้งหนี้/)).toBeVisible();
  expect(screen.queryByRole("button", { name: previewName })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
  canView = true;
  await user.click(screen.getByRole("button", { name: lookupName }));
  expect(await screen.findByText(/INV-FOUND/)).toBeVisible();
  expect(submit).toHaveBeenCalledTimes(1);
});
