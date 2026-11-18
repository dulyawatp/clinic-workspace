import { afterEach, expect, it, vi } from "vitest";
import { findProcedures, viewProcedure } from "./procedure-read";

const item = { procedure_id: "PROC/1 & 2", status: "Pending", docstatus: 0 };

afterEach(() => vi.restoreAllMocks());

it("reads the exact status shape using an encoded procedure ID", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ message: { procedure: item } })),
  );
  await expect(viewProcedure(item.procedure_id)).resolves.toEqual(item);
  expect(fetch.mock.calls[0]?.[0]).toBe("/api/method/clinic_ui.api.procedures_write.view?procedure_id=PROC%2F1+%26+2");
});

it("reads an exact list using an encoded service request", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ message: { items: [item] } })),
  );
  await expect(findProcedures("SR/1 & 2")).resolves.toEqual([item]);
  expect(fetch.mock.calls[0]?.[0]).toBe("/api/method/clinic_ui.api.procedures_write.by_service_request?service_request=SR%2F1+%26+2");
});

it.each([401, 403])("reports permission failure for HTTP %i", async (status) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status }));
  await expect(viewProcedure("PROC")).rejects.toMatchObject({ kind: "permission" });
  await expect(findProcedures("SR")).rejects.toMatchObject({ kind: "permission" });
});

it.each([
  { message: { procedure: { ...item, extra: true } } },
  { message: { procedure: { procedure_id: "PROC", status: "Pending" } } },
  { message: { procedure: { ...item, docstatus: "0" } } },
  { message: { procedure: item, extra: true } },
])("rejects malformed status responses", async (body) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body)));
  await expect(viewProcedure("PROC")).rejects.toMatchObject({ kind: "unavailable" });
});

it("rejects malformed lookup results", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ message: { items: [{ ...item, docstatus: null }] } })),
  );
  await expect(findProcedures("SR")).rejects.toMatchObject({ kind: "unavailable" });
});
