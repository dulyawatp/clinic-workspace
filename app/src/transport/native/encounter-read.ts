import { z } from "zod";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";
import { classifyNativeForbidden } from "./session";

const documentId = z.string().refine((value) => value.trim() !== "");
const summary = z.object({
  id: documentId,
  patientId: documentId,
  patientName: documentId,
  practitioner: z.string().nullable(),
  encounterDate: z.iso.date().nullable(),
  docstatus: z.union([z.literal(0), z.literal(1), z.literal(2)]),
});
const detail = summary.extend({
  symptoms: z.array(z.string()).nullable(),
  diagnoses: z.array(z.string()).nullable(),
});
const page = z.object({
  items: z.array(summary).max(20),
  nextOffset: z.number().int().min(1).max(1_000_000).nullable(),
});

export type NativeEncounterSummary = z.infer<typeof summary>;
export type NativeEncounterDetail = z.infer<typeof detail>;
export type NativeEncounterPage = z.infer<typeof page>;
export type EncounterReadError = AppError | { code: "MALFORMED"; retryable: true };

const malformed: EncounterReadError = { code: "MALFORMED", retryable: true };

async function read(method: string, params: Record<string, string>, signal: AbortSignal) {
  return frappeRequest(`clinic_ui.api.encounters.${method}?${new URLSearchParams(params)}`, {
    method: "GET",
    signal,
  });
}

async function message(response: Response, signal: AbortSignal): Promise<unknown> {
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (!response.ok) throw mapHttpError(response.status, null);
  const envelope: unknown = await response.json().catch(() => null);
  if (typeof envelope !== "object" || !envelope || !("message" in envelope)) throw malformed;
  return envelope.message;
}

export async function listNativeEncounters(
  patientRef: string,
  offset: number,
  signal: AbortSignal,
): Promise<NativeEncounterPage> {
  const response = await read("list_for_patient", { patient_ref: patientRef, offset: String(offset) }, signal);
  const parsed = page.safeParse(await message(response, signal));
  if (!parsed.success || (parsed.data.nextOffset !== null && parsed.data.nextOffset <= offset)) throw malformed;
  return parsed.data;
}

export async function viewNativeEncounter(id: string, signal: AbortSignal): Promise<NativeEncounterDetail | null> {
  const response = await read("view", { encounter_id: id }, signal);
  if (response.status === 404) return null;
  const parsed = detail.safeParse(await message(response, signal));
  if (!parsed.success || parsed.data.id !== id) throw malformed;
  return parsed.data;
}
