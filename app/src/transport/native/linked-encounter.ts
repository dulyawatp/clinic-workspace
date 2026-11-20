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
});

export type LinkedEncounter = z.infer<typeof projection>;
export type LinkedNote = z.infer<typeof note>;
export type LinkedClinical = {
  symptoms?: string[];
  diagnoses?: string[];
  note?: { id: string | null; modified: string | null; text: string };
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
  const { notes, encounter, context } = result;
  if ((!alias.test(ref) && context.appointment !== ref) ||
      (!encounter && (notes.state !== "available" || notes.items.length || notes.next_offset !== null)) ||
      (notes.next_offset !== null && notes.next_offset <= offset) ||
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
