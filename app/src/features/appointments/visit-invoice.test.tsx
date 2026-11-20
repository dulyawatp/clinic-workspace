import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
const preview = (token: string, cut = "0.00", net = "100.00", vat = "7.00", total = "107.00") =>
  HttpResponse.json({ message: { currency: "USD", gross: "100.00", discount: cut, net, vat, total, preview_token: token } });

function page() {
  renderWithFixtures(
    <Routes><Route path="/appointments/:id" element={<AppointmentPage />} /></Routes>,
    { path: `/appointments/${alias}`, role: "DOC" },
  );
}

function native(caps: Record<string, string>, role = "DOC", hasExactId = true, noVatAllowed?: unknown) {
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
        ...(noVatAllowed === undefined ? {} : { visit_invoice_no_vat_allowed: noVatAllowed }),
      } }),
    ),
  );
}

const previewName = "ตรวจสอบยอดใบแจ้งหนี้";
const confirmName = "ยืนยันออกใบแจ้งหนี้";
const lookupName = "ค้นหาใบแจ้งหนี้ของนัดหมาย";
const title = "ใบแจ้งหนี้สำหรับนัดหมาย";
const vatName = "ภาษีมูลค่าเพิ่ม";
const noVatName = "ไม่คิด VAT";

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

it("defaults to 7% VAT when only the submit capability is enabled", async () => {
  native(send);
  const seen = vi.fn();
  server.use(http.post(`${base}.preview`, async ({ request }) => {
    seen(await request.json());
    return preview("none");
  }));
  page();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: previewName }));
  expect(await screen.findByText("USD 0.00")).toBeVisible();
  expect(await screen.findByText("USD 107.00")).toBeVisible();
  expect(seen).toHaveBeenCalledExactlyOnceWith({ appointment_id: appointment, vat_mode: "vat_7" });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByText(noVatName)).not.toBeInTheDocument();
});

it.each([undefined, false, "true"])("does not grant No VAT from write or discount roles with flag %s", async (flag) => {
  native({ ...send, ...discount }, "DOC", true, flag);
  page();
  const region = within(await screen.findByRole("region", { name: title }));
  expect(region.getByText("คิด VAT 7%")).toBeVisible();
  expect(region.queryByRole("combobox", { name: vatName })).not.toBeInTheDocument();
  expect(region.queryByText(noVatName)).not.toBeInTheDocument();
});

it("defaults to VAT for eligible visits, invalidates previewed totals on change, and submits No VAT", async () => {
  native(send, "DOC", true, true);
  const previews = vi.fn();
  const submits = vi.fn();
  server.use(
    http.post(`${base}.preview`, async ({ request }) => {
      previews(await request.json());
      return previews.mock.calls.length === 1 ? preview("with-vat") : preview("without-vat", "0.00", "100.00", "0.00", "100.00");
    }),
    http.post(`${base}.submit`, async ({ request }) => {
      submits(await request.json());
      return HttpResponse.json({ message: { invoice_id: "INV-NO-VAT" } });
    }),
  );
  page();
  const user = userEvent.setup();
  const region = within(await screen.findByRole("region", { name: title }));
  const vat = region.getByRole("combobox", { name: vatName });
  expect(vat).toHaveValue("vat_7");
  expect(region.getByRole("option", { name: noVatName })).toBeEnabled();
  await user.click(region.getByRole("button", { name: previewName }));
  expect(await region.findByText("USD 107.00")).toBeVisible();
  await user.selectOptions(vat, "no_vat");
  expect(region.queryByText("USD 107.00")).not.toBeInTheDocument();
  expect(region.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
  await user.click(region.getByRole("button", { name: previewName }));
  const confirm = await region.findByRole("button", { name: confirmName });
  expect(region.getByText(vatName, { selector: "dt" }).nextElementSibling).toHaveTextContent("USD 0.00");
  expect(region.getByText("ยอดที่ต้องชำระ").nextElementSibling).toHaveTextContent("USD 100.00");
  await user.click(confirm);
  expect(await region.findByText(/INV-NO-VAT/)).toBeVisible();
  expect(previews.mock.calls).toEqual([
    [{ appointment_id: appointment, vat_mode: "vat_7" }],
    [{ appointment_id: appointment, vat_mode: "no_vat" }],
  ]);
  expect(submits).toHaveBeenCalledExactlyOnceWith({
    appointment_id: appointment, vat_mode: "no_vat", preview_token: "without-vat",
  });
});

it.each(["VAT", "discount"] as const)("ignores an in-flight preview after a %s edit, even if changed back", async (field) => {
  native({ ...send, ...discount }, "DOC", true, true);
  let finish: (() => void) | undefined;
  const inFlight = new Promise<void>((resolve) => { finish = resolve; });
  const seen = vi.fn();
  server.use(http.post(`${base}.preview`, async ({ request }) => {
    seen(await request.json());
    await inFlight;
    return preview("superseded");
  }));
  page();
  const user = userEvent.setup();
  const region = within(await screen.findByRole("region", { name: title }));
  await user.click(region.getByRole("button", { name: previewName }));
  expect(region.getByRole("button", { name: previewName })).toBeDisabled();
  const select = region.getByRole("combobox", { name: field === "VAT" ? vatName : "ส่วนลด" });
  await user.selectOptions(select, field === "VAT" ? "no_vat" : "percent");
  expect(select).toHaveValue(field === "VAT" ? "no_vat" : "percent");
  await user.selectOptions(select, field === "VAT" ? "vat_7" : "none");
  finish?.();
  await waitFor(() => expect(region.getByRole("button", { name: previewName })).toBeEnabled());
  expect(region.queryByText("USD 107.00")).not.toBeInTheDocument();
  expect(region.queryByRole("button", { name: confirmName })).not.toBeInTheDocument();
  expect(seen).toHaveBeenCalledExactlyOnceWith({ appointment_id: appointment, vat_mode: "vat_7" });
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
      return previews.mock.calls.length === 1
        ? preview("amount", "12.50", "87.50", "6.13", "93.63")
        : preview("percent", "15.00", "85.00", "5.95", "90.95");
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
  expect(await region.findByText("USD 90.95")).toBeVisible();
  for (const [label, value] of [
    ["ค่าตรวจ", "100.00"],
    ["ส่วนลด", "15.00"],
    ["ยอดหลังหักส่วนลด (ก่อน VAT)", "85.00"],
    [vatName, "5.95"],
    ["ยอดที่ต้องชำระ", "90.95"],
  ] as const) {
    expect(region.getByText(label, { selector: "dt" }).nextElementSibling).toHaveTextContent(`USD ${value}`);
  }
  const confirm = region.getByRole("button", { name: confirmName });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(confirm).toBeDisabled();
  expect(region.getByRole("button", { name: lookupName })).toBeDisabled();
  finish?.();
  expect(await region.findByText(/INV-OK/)).toBeVisible();
  expect(previews.mock.calls).toEqual([
    [{ appointment_id: appointment, vat_mode: "vat_7", discount: { mode: "amount", value: "12.50" } }],
    [{ appointment_id: appointment, vat_mode: "vat_7", discount: { mode: "percent", value: "15" } }],
  ]);
  expect(submits).toHaveBeenCalledExactlyOnceWith({
    appointment_id: appointment, vat_mode: "vat_7", discount: { mode: "percent", value: "15" }, preview_token: "percent",
  });
});

it("removes confirmation and reports normalized validation without claiming uncertainty", async () => {
  native(send);
  server.use(
    http.post(`${base}.preview`, () => preview("none")),
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
    http.post(`${base}.preview`, () => preview("none")),
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
