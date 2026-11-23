import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealthcareInvoiceContext, prepareHealthcareInvoice, readHealthcareInvoice } from "./native-healthcare-invoice";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const service = { service: "Consult", reference_type: "Patient Appointment", reference_name: "APT-2", qty: 2, rate: 100, insurance_coverage: "COV-1" };

function setup() {
  return vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient Appointment", name: "APT-1", patient: "PAT-1", company: "Hospital", status: "Open" } }))
    .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient", name: "PAT-1", customer: "CUST-1" } }))
    .mockResolvedValueOnce(Response.json({ message: [service] }));
}

describe("native healthcare invoice", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reads native appointment and patient before requesting patient-wide services without linking customer", async () => {
    const fetch = setup();
    const context = await getHealthcareInvoiceContext("APT-1");
    expect(context).toMatchObject({ appointment: "APT-1", patient: "PAT-1", company: "Hospital", customer: "CUST-1", rows: [service] });
    const urls = fetch.mock.calls.map(([url]) => String(url));
    expect(urls[0]).toContain("frappe.client.get?doctype=Patient+Appointment&name=APT-1");
    expect(urls[1]).toContain("frappe.client.get?doctype=Patient&name=PAT-1");
    expect(urls[2]).toContain("healthcare.healthcare.utils.get_healthcare_services_to_invoice?");
    const params = new URL(urls[2]!, "https://example.com").searchParams;
    expect(Object.fromEntries(params)).toEqual({ patient: "PAT-1", customer: "CUST-1", company: "Hospital" });
  });

  it("rejects bad read shapes and missing real patient customer", async () => {
    const fetch = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ message: { name: "APT-1", patient: "PAT-1", company: "Hospital" } }))
      .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient Appointment", name: "APT-1", patient: "PAT-1", company: "Hospital" } }))
      .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient", name: "PAT-1", customer: "" } }));
    await expect(getHealthcareInvoiceContext("APT-1")).rejects.toThrow();
    await expect(getHealthcareInvoiceContext("APT-1")).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("dispatches selected native services through run_doc_method, preserving returned native fields", async () => {
    const fetch = setup();
    const context = await getHealthcareInvoiceContext("APT-1");
    const doc = { doctype: "Sales Invoice", docstatus: 0, patient: "PAT-1", customer: "CUST-1", company: "Hospital", items: [{ item_code: "Consult", qty: 2, reference_dt: "Patient Appointment", reference_dn: "APT-2", insurance_coverage: "COV-1" }], taxes: [{ rate: 7 }] };
    fetch.mockResolvedValueOnce(Response.json({ docs: [doc] }));
    const selected = context.rows[0]!;
    await expect(prepareHealthcareInvoice(context, [selected])).resolves.toEqual(doc);
    const [url, options] = fetch.mock.calls[3]!;
    expect(url).toBe("/api/method/run_doc_method");
    expect(options).toMatchObject({ method: "POST", headers: { "content-type": "application/json", "X-Frappe-CSRF-Token": "csrf" } });
    expect(JSON.parse(String(options?.body))).toEqual({
      docs: { doctype: "Sales Invoice", docstatus: 0, __islocal: 1, patient: "PAT-1", customer: "CUST-1", company: "Hospital", update_stock: 0, items: [] },
      method: "set_healthcare_services",
      args: { checked_values: [{ dn: "APT-2", dt: "Patient Appointment", item: "Consult", qty: "2", rate: "100", income_account: false, description: false, discount_percentage: false, coverage_rate: false, coverage_qty: false, insurance_payor: false, patient_insurance_policy: false, coverage_percentage: false, insurance_coverage: "COV-1" }] },
    });
    await expect(prepareHealthcareInvoice(context, [])).rejects.toThrow("Select");
    await expect(prepareHealthcareInvoice(context, [{ ...selected, service: "OTHER" }])).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("rejects preparation failures rather than presenting them as empty services", async () => {
    const fetch = setup();
    const context = await getHealthcareInvoiceContext("APT-1");
    fetch.mockResolvedValueOnce(Response.json({ message: [] }));
    await expect(prepareHealthcareInvoice(context, context.rows)).rejects.toThrow("preparation");
    session.token = null;
    await expect(prepareHealthcareInvoice(context, context.rows)).rejects.toThrow("CSRF");
    session.token = "csrf";
  });

  it("accepts native rows without an item or quantity while refusing to prepare an unbillable row", async () => {
    const fetch = setup();
    fetch.mockReset()
      .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient Appointment", name: "APT-1", patient: "PAT-1", company: "Hospital" } }))
      .mockResolvedValueOnce(Response.json({ message: { doctype: "Patient", name: "PAT-1", customer: "CUST-1" } }))
      .mockResolvedValueOnce(Response.json({ message: [{ service: null, reference_type: "Lab Test", reference_name: "LAB-1" }] }));
    const context = await getHealthcareInvoiceContext("APT-1");
    expect(context.rows).toHaveLength(1);
    await expect(prepareHealthcareInvoice(context, context.rows)).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("reads exact persisted draft or submitted invoice and rejects mismatches", async () => {
    const doc = { doctype: "Sales Invoice", name: "SINV-1", docstatus: 0, items: [] };
    const fetch = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ message: doc }))
      .mockResolvedValueOnce(Response.json({ message: { ...doc, docstatus: 1 } }))
      .mockResolvedValueOnce(Response.json({ message: { ...doc, name: "OTHER" } }));
    await expect(readHealthcareInvoice("SINV-1")).resolves.toEqual(doc);
    await expect(readHealthcareInvoice("SINV-1")).resolves.toMatchObject({ docstatus: 1 });
    await expect(readHealthcareInvoice("SINV-1")).rejects.toThrow();
    expect(String(fetch.mock.calls[0]?.[0])).toContain("frappe.client.get?doctype=Sales+Invoice&name=SINV-1");
  });
});
