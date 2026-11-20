import { HttpResponse, http } from "msw";
import { afterEach, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { clearNativeCsrfToken, fetchNativeSession } from "./session";
import { parseNativeAppointmentRead } from "./appointment";
import { previewVisitInvoice, submitVisitInvoice, viewVisitInvoice } from "./visit-invoice";

const base = "/api/method/clinic_ui.api.visit_invoice";
const input = { appointment_id: "VISIT/& 1", vat_mode: "vat_7", discount: { mode: "amount", value: "12.50" } } as const;
const preview = {
  currency: "USD", gross: "100.00", discount: "12.50",
  net: "87.50", vat: "6.13", total: "93.63", preview_token: "token",
};

async function csrf() {
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
      HttpResponse.json({ message: {
        mode: "live", sessionEpoch: "native", user: { id: "u1", displayName: "Doctor", roles: ["DOC"] },
        capabilities: {}, csrfToken: "csrf",
      } }),
    ),
  );
  await fetchNativeSession();
}

afterEach(clearNativeCsrfToken);

it("requires an explicit native visit anchor and literal true to offer No VAT", () => {
  const detail = {
    appointment_id: "alias", workspace_patient_id: "patient", appointment_date: "2026-11-20",
    appointment_time: "09:00", status: "Open", modified: "2026-11-20 09:00:00",
    can_reschedule: false, can_checkin: false,
  };
  expect(parseNativeAppointmentRead(detail).visit_invoice_appointment_id).toBeUndefined();
  const anchored = { ...detail, visit_invoice_appointment_id: input.appointment_id };
  expect(parseNativeAppointmentRead(anchored, "alias"))
    .toMatchObject({ appointment_id: "alias", visit_invoice_appointment_id: input.appointment_id });
  expect(parseNativeAppointmentRead(anchored).visit_invoice_no_vat_allowed).toBeUndefined();
  expect(parseNativeAppointmentRead({ ...anchored, visit_invoice_no_vat_allowed: true })
    .visit_invoice_no_vat_allowed).toBe(true);
  for (const flag of [false, "true", 1, null]) {
    expect(parseNativeAppointmentRead({ ...anchored, visit_invoice_no_vat_allowed: flag })
      .visit_invoice_no_vat_allowed).toBeUndefined();
  }
  expect(() => parseNativeAppointmentRead({ ...detail, visit_invoice_no_vat_allowed: true })).toThrow();
  expect(() => parseNativeAppointmentRead({ ...detail, visit_invoice_appointment_id: " \n" })).toThrow();
});

it.each(["vat_7", "no_vat"] as const)("sends exact %s POSTs with native CSRF and encodes the view query", async (vat_mode) => {
  await csrf();
  const requestInput = { ...input, vat_mode };
  const result = vat_mode === "vat_7" ? preview : { ...preview, vat: "0.00", total: preview.net };
  server.use(
    http.post(`${base}.preview`, async ({ request }) => {
      expect(request.headers.get("X-Frappe-CSRF-Token")).toBe("csrf");
      expect(await request.json()).toEqual(requestInput);
      return HttpResponse.json({ message: result });
    }),
    http.post(`${base}.submit`, async ({ request }) => {
      expect(request.headers.get("X-Frappe-CSRF-Token")).toBe("csrf");
      expect(await request.json()).toEqual({ ...requestInput, preview_token: "token" });
      return HttpResponse.json({ message: { invoice_id: "INV-1" } });
    }),
    http.get(`${base}.view`, ({ request }) => {
      expect([...new URL(request.url).searchParams]).toEqual([["appointment_id", input.appointment_id]]);
      return HttpResponse.json({ message: { invoice_id: "INV-1" } });
    }),
  );
  expect(await previewVisitInvoice(requestInput)).toEqual(result);
  expect(await submitVisitInvoice({ ...requestInput, preview_token: "token" })).toEqual({ invoice_id: "INV-1" });
  expect(await viewVisitInvoice(input.appointment_id)).toEqual({ invoice_id: "INV-1" });
});

it.each([
  { total: 87.5 }, { discount: "12.5" }, { gross: "1e2" }, { currency: " " },
  { net: "87.50\n" }, { vat: "6.1" }, { vat: 6.13 }, { net: undefined }, { vat: undefined },
  { net: "90.00" }, { total: "90.00" }, { preview_token: "" }, { customer: "private" },
])("rejects invalid preview fields: %j", async (override) => {
  await csrf();
  server.use(http.post(`${base}.preview`, () => HttpResponse.json({ message: { ...preview, ...override } })));
  await expect(previewVisitInvoice(input)).rejects.toMatchObject({ code: "unavailable" });
});

it("rejects tax in a No VAT preview even when the totals add up", async () => {
  await csrf();
  server.use(http.post(`${base}.preview`, () => HttpResponse.json({ message: preview })));
  await expect(previewVisitInvoice({ ...input, vat_mode: "no_vat" })).rejects.toMatchObject({ code: "unavailable" });
});

it.each([417, 422])("recognizes normalized validation at HTTP %i", async (status) => {
  await csrf();
  server.use(http.post(`${base}.submit`, () => HttpResponse.json({ code: "VALIDATION" }, { status })));
  await expect(submitVisitInvoice({ ...input, preview_token: "token" })).rejects.toMatchObject({ code: "validation" });
});

it.each([
  ["root conflict", () => HttpResponse.json({ code: "WRITE_UNCERTAIN" }, { status: 409 })],
  ["wrapped conflict", () => HttpResponse.json({ message: { code: "WRITE_UNCERTAIN" } }, { status: 409 })],
  ["unexpected validation body", () => HttpResponse.json({ code: "OTHER" }, { status: 417 })],
  ["unwrapped 2xx", () => HttpResponse.json({ invoice_id: "INV-1" })],
  ["unrecognized 2xx", () => HttpResponse.json({ message: { invoice_id: "INV-1", extra: true } })],
  ["invalid JSON", () => HttpResponse.text("<html>bad gateway</html>")],
  ["lost response", () => HttpResponse.error()],
] as const)("treats %s as uncertain without retry", async (_, response) => {
  await csrf();
  let count = 0;
  server.use(http.post(`${base}.submit`, () => {
    count++;
    return response();
  }));
  await expect(submitVisitInvoice({ ...input, preview_token: "token" })).rejects.toMatchObject({ code: "uncertain" });
  expect(count).toBe(1);
});

it.each([
  () => HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
  () => HttpResponse.json({ message: {} }),
  () => HttpResponse.json({ message: { invoice_id: "INV-1", customer: "private" } }),
])("treats unreadable lookup as generic unavailable", async (response) => {
  server.use(http.get(`${base}.view`, response));
  await expect(viewVisitInvoice("VISIT-1")).rejects.toMatchObject({ code: "unavailable" });
});
