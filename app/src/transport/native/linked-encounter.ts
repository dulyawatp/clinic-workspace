import { z } from "zod";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import type { EncounterReadError } from "./encounter-read";
import { frappeRequest, FRAPPE_CSRF_HEADER } from "./frappe";
import { classifyNativeForbidden, nativeCsrfToken } from "./session";

const id = z.string().max(256).refine((value) =>
  value.trim() !== "" && value.trim() === value &&
    !Array.from(value).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127));
const version = z.string().refine((value) => value.trim() !== "");
const note = z.object({ id, modified: version, text: z.string() });
const reference = z.union([id, z.literal(""), z.null()]);
const check = z.union([z.literal(0), z.literal(1), z.boolean(), z.null()]);
const medication = z.object({
  id,
  drug_code: reference,
  dosage: reference,
  period: reference,
  dosage_form: reference,
  medication_request: reference,
  supported: z.boolean(),
  details: z.object({
    comment: z.string().nullable(),
    interval: z.number().int().nullable(),
    interval_uom: z.string().nullable(),
    strength: z.number().nullable(),
    strength_uom: reference,
    dosage_by_interval: check,
    number_of_repeats_allowed: z.number().nullable(),
    intent: reference,
    priority: reference,
    update_schedule: check,
  }),
}).refine((row) => {
  if (!row.supported) return true;
  const { update_schedule, ...nonstandard } = row.details;
  return Boolean(row.drug_code && row.dosage && row.period && row.dosage_form && !row.medication_request) &&
    update_schedule !== false && update_schedule !== 0 && !Object.values(nonstandard).some(Boolean);
});
const medications = z.discriminatedUnion("state", [
  z.object({ state: z.literal("available"), complete: z.boolean(), items: z.array(medication).max(100) }),
  z.object({ state: z.literal("unavailable"), complete: z.literal(false), items: z.tuple([]) }),
]);
const projection = z.object({
  context: z.object({ appointment: id, patient: id, practitioner: id, status: z.string().min(1) }),
  encounter: z.object({
    id, modified: version, docstatus: z.union([z.literal(0), z.literal(1)]),
    symptoms: z.array(id), diagnoses: z.array(id),
  }).nullable(),
  notes: z.discriminatedUnion("state", [
    z.object({ state: z.literal("available"), items: z.array(note).max(20), next_offset: z.number().int().positive().nullable() }),
    z.object({ state: z.literal("unavailable"), items: z.tuple([]), next_offset: z.null() }),
  ]),
  medications,
});

export type LinkedEncounter = z.infer<typeof projection>;
export type LinkedNote = z.infer<typeof note>;
export type LinkedMedication = z.infer<typeof medication>;
export type LinkedMedications = z.infer<typeof medications>;
export type PrescriptionDosage = { dosage: string; period: string; dosage_form: string };
export type MedicationChanges = {
  add: (PrescriptionDosage & { drug_code: string })[];
  edit: (PrescriptionDosage & { id: string })[];
};
export type LinkedClinical = {
  symptoms?: string[];
  diagnoses?: string[];
  note?: { id: string | null; modified: string | null; text: string };
  medications?: MedicationChanges;
};
export const invalidLinkedEncounter = { code: "MALFORMED", retryable: false } as const;
const method = "clinic_ui.api.encounters.";
const alias = /^wsa1_[0-9a-f]{32}_[0-9a-f]{16}_[A-Za-z0-9_-]{16,128}$/;

export function isAppointmentRef(value: string): boolean {
  return id.safeParse(value).success && (!value.startsWith("wsa1_") || alias.test(value));
}

async function json(response: Response, signal?: AbortSignal) {
  if (response.status === 403 && signal) throw await classifyNativeForbidden(signal);
  if (response.status === 417) throw { code: "VALIDATION", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, null);
  return response.json().catch(() => { throw invalidLinkedEncounter; });
}

async function linkedProjection(
  response: Response, ref: string, offset: number, signal?: AbortSignal,
): Promise<LinkedEncounter> {
  const parsed = z.object({ message: projection }).safeParse(await json(response, signal));
  if (!parsed.success) throw invalidLinkedEncounter;
  const result = parsed.data.message;
  const { notes, encounter, context, medications: prescriptions } = result;
  if ((!alias.test(ref) && context.appointment !== ref) ||
      (!encounter && (notes.state !== "available" || notes.items.length || notes.next_offset !== null)) ||
      (!encounter && (!prescriptions.complete || prescriptions.items.length)) ||
      (notes.next_offset !== null && notes.next_offset <= offset) ||
      new Set(prescriptions.items.map((item) => item.id)).size !== prescriptions.items.length ||
      new Set(notes.items.map((item) => item.id)).size !== notes.items.length) throw invalidLinkedEncounter;
  return result;
}

export async function openLinkedEncounter(ref: string, signal: AbortSignal, noteOffset = 0) {
  const params = new URLSearchParams({ appointment_ref: ref, note_offset: String(noteOffset) });
  const response = await frappeRequest(`${method}open_for_appointment?${params}`, { signal });
  return linkedProjection(response, ref, noteOffset, signal);
}

async function write(action: "save_linked" | "complete_linked", ref: string, body: object) {
  const token = nativeCsrfToken();
  if (!token) throw { code: "FORBIDDEN", retryable: false } satisfies EncounterReadError;
  const response = await frappeRequest(`${method}${action}`, {
    method: "POST",
    redirect: "error",
    headers: { "Content-Type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({ appointment_ref: ref, ...body }),
  });
  return linkedProjection(response, ref, 0);
}

export function saveLinkedEncounter(ref: string, expectedVersion: string | null, clinical: LinkedClinical) {
  return write("save_linked", ref, { expected_version: expectedVersion, clinical });
}

export function completeLinkedEncounter(ref: string, encounterId: string, expectedVersion: string) {
  return write("complete_linked", ref, { encounter_id: encounterId, expected_version: expectedVersion });
}

/** Stock Frappe resource reads enforce master permissions. Search selects docnames only. */
export async function searchClinicalMaster(
  doctype: "Complaint" | "Diagnosis", search: string, signal: AbortSignal,
): Promise<string[]> {
  if (/[%_\\]/.test(search)) throw { code: "VALIDATION", retryable: false } satisfies AppError;
  const params = new URLSearchParams({
    fields: JSON.stringify(["name"]),
    filters: JSON.stringify([["name", "like", `%${search}%`]]),
    order_by: "name asc",
    limit_page_length: "20",
  });
  const response = await fetch(`/api/resource/${doctype}?${params}`, {
    signal, credentials: "same-origin", cache: "no-store", headers: { accept: "application/json" },
  });
  const parsed = z.object({ data: z.array(z.object({ name: id })).max(20) }).safeParse(await json(response, signal));
  if (!parsed.success) throw invalidLinkedEncounter;
  return [...new Set(parsed.data.data.map(({ name }) => name))];
}

export type PrescriptionMaster = "Item" | "Prescription Dosage" | "Prescription Duration" | "Dosage Form";
export type PrescriptionChoice = { id: string; label: string };

/** Ordinary Frappe list permissions. Never query Medication or its linked-item tables. */
export async function searchPrescriptionMaster(
  doctype: PrescriptionMaster, text: string, signal: AbortSignal,
): Promise<PrescriptionChoice[]> {
  if (text.length > 100 || !id.safeParse(text).success) throw { code: "VALIDATION", retryable: false } satisfies AppError;
  const item = doctype === "Item";
  const exact = /[%_\\]/.test(text);
  const match = (field: string) => [field, exact ? "=" : "like", exact ? text : `%${text}%`];
  const params = new URLSearchParams({
    fields: JSON.stringify(item ? ["name", "item_name"] : ["name"]),
    // These two Item filters are used by pinned Marley's prescription query.
    // Its separate Medication Linked Item join is not an authorized client catalog.
    filters: JSON.stringify(item ? [["disabled", "=", 0], ["is_stock_item", "=", 1]] : [match("name")]),
    order_by: "name asc",
    limit_page_length: "20",
  });
  if (item) params.set("or_filters", JSON.stringify([match("name"), match("item_name")]));
  const response = await fetch(`/api/resource/${encodeURIComponent(doctype)}?${params}`, {
    signal, credentials: "same-origin", cache: "no-store", headers: { accept: "application/json" },
  });
  const entry = item ? z.object({ name: id, item_name: z.string().nullable() }) : z.object({ name: id });
  const parsed = z.object({ data: z.array(entry).max(20) }).safeParse(await json(response, signal));
  if (!parsed.success || new Set(parsed.data.data.map(({ name }) => name)).size !== parsed.data.data.length) {
    throw invalidLinkedEncounter;
  }
  return parsed.data.data.map((row) => ({
    id: row.name,
    label: "item_name" in row && typeof row.item_name === "string" && row.item_name.trim() ? row.item_name : row.name,
  }));
}
