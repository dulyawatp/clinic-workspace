import { z } from "zod";
import { mapHttpError } from "../error-map";
import type { EncounterReadError } from "./encounter-read";
import { frappeRequest, FRAPPE_CSRF_HEADER } from "./frappe";
import { classifyNativeForbidden, nativeCsrfToken } from "./session";

const doctype = "Clinical Note";
const reference = "Patient Encounter";
const pageSize = 10;
const malformed: EncounterReadError = { code: "MALFORMED", retryable: true };
const name = z.string().max(256).refine((value) => value !== "XXXXXXXX" && Boolean(value.trim()) && !/[\u0000-\u001f\u007f]/.test(value));
const names = z.array(z.object({ name })).max(pageSize + 1);
const note = z.object({
  name,
  doctype: z.literal(doctype),
  patient: name,
  reference_doc: z.literal(reference),
  reference_name: name,
  // Frappe substitutes this sentinel when the Text Editor field is masked.
  note: z.string().refine((value) => value !== "XXXXXXXX"),
});

export type HistoryNotePage = {
  items: { id: string; text: string }[];
  nextOffset: number | null;
};

async function read(path: "get" | "get_list", params: object, signal: AbortSignal): Promise<unknown> {
  const token = nativeCsrfToken();
  if (!token) throw { code: "FORBIDDEN", retryable: false } satisfies EncounterReadError;
  // These RPCs only read. Use POST to keep patient-derived identifiers out of URLs.
  const response = await frappeRequest(`frappe.client.${path}`, {
    method: "POST", redirect: "error", signal,
    headers: { "Content-Type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({ doctype, ...params }),
  });
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (!response.ok) throw mapHttpError(response.status, null);
  const envelope = await response.json().catch(() => null);
  if (!envelope || typeof envelope !== "object" || !("message" in envelope)) throw malformed;
  return envelope.message;
}

export async function listHistoryNotes(
  encounterId: string, patientId: string, offset: number, signal: AbortSignal,
): Promise<HistoryNotePage> {
  // Opaque production patients require a server-side note projection, not generic resource reads.
  if (patientId.startsWith("cpr1_")) throw malformed;
  if (!name.safeParse(encounterId).success || !name.safeParse(patientId).success ||
      !Number.isSafeInteger(offset + pageSize) || offset < 0) throw malformed;
  const parsed = names.safeParse(await read("get_list", {
    filters: { reference_doc: reference, reference_name: encounterId, patient: patientId },
    fields: ["name"],
    order_by: "name asc",
    limit_start: offset,
    limit_page_length: pageSize + 1,
  }, signal));
  if (!parsed.success || new Set(parsed.data.map((row) => row.name)).size !== parsed.data.length) throw malformed;

  // Listing may allow select-only access. Read each exact document to apply
  // native document read permission and field stripping/masking before use.
  const items = await Promise.all(parsed.data.slice(0, pageSize).map(async (row) => {
    const result = note.safeParse(await read("get", { name: row.name }, signal));
    if (!result.success || result.data.name !== row.name || result.data.patient !== patientId ||
        result.data.reference_name !== encounterId) throw malformed;
    return { id: result.data.name, text: result.data.note };
  }));
  return { items, nextOffset: parsed.data.length > pageSize ? offset + pageSize : null };
}
