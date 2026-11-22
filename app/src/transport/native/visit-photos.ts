import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const method = "clinic_photos.api.visit_photos.";
const optionsMethod = "clinic_photos.api.visit_options.capture_options";
const uploadMethod = "clinic_photos.api.visit_upload.";
const reference = z.string().min(1).max(140).refine((value) =>
  value !== "XXXXXXXX" && value.trim() === value && !/[\p{Cc}\p{Cf}]/u.test(value),
);
const snapshot = z.strictObject({ photo: reference, body_area: z.string(), viewpoint: z.string() });
const pair = z.strictObject({ appointment: reference, before: snapshot.nullable(), after: snapshot.nullable(),
  drawings_enabled: z.literal(true).optional() });
const encounter = z.object({
  doctype: z.literal("Patient Encounter"),
  name: reference,
  patient: reference,
  appointment: reference,
});
const envelope = z.object({ message: z.unknown() });
const signatures = {
  "image/png": [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
  "image/jpeg": [0xff, 0xd8, 0xff],
} as const;
const maxImageBytes = 8 * 1024 * 1024;
const options = z.strictObject({
  has_active_consent: z.boolean(),
  can_grant: z.boolean(),
  can_revoke: z.boolean(),
  can_capture_before: z.boolean(),
  can_capture_after: z.boolean(),
});
const status = z.strictObject({ state: z.enum(["not_found", "pending", "unknown", "rejected", "saved"]) });
const staged = z.strictObject({ state: z.literal("pending") });
const consent = z.strictObject({ ok: z.literal(true) });

export type VisitPhotoSlot = "Before" | "After";
export type VisitPhotoOptions = z.infer<typeof options>;
export type VisitPhotoSlotStatus = z.infer<typeof status>["state"];
export type VisitPhotoFraming = { body_area: string; viewpoint: string };
export type VisitPhotoTarget = { appointment: string; encounter?: string };

async function request(method: string, body: object, signal?: AbortSignal, accept = "application/json") {
  const csrf = nativeCsrfToken();
  if (!csrf) return null;
  const response = await frappeRequest(method, {
    method: "POST",
    mode: "same-origin",
    redirect: "error",
    signal,
    headers: { accept, "Content-Type": "application/json", [FRAPPE_CSRF_HEADER]: csrf },
    body: JSON.stringify(body),
  });
  if (response.status !== 200 || response.redirected ||
      (response.url && response.url !== new URL(`/api/method/${method}`, location.origin).href)) return null;
  return response;
}

async function message(response: Response | null) {
  if (!response) return null;
  return envelope.safeParse(await response.json().catch(() => null)).data?.message;
}

export async function readVisitPhotoAppointment(encounterId: string, patientId: string, signal: AbortSignal) {
  if (!reference.safeParse(encounterId).success || !reference.safeParse(patientId).success) return null;
  // The history projection has no appointment field. Read the exact authorized
  // Encounter; never infer a binding from the patient's other appointments.
  const response = await request("frappe.client.get", { doctype: "Patient Encounter", name: encounterId }, signal);
  const doc = encounter.safeParse(await message(response)).data;
  return doc && doc.name === encounterId && doc.patient === patientId ? doc.appointment : null;
}

export async function readVisitPhotoPair(appointment: string, signal: AbortSignal, includeFraming = false) {
  if (!reference.safeParse(appointment).success) return null;
  const response = await request(`${method}read_pair`, { payload: { appointment } }, signal);
  const result = pair.safeParse(await message(response)).data;
  if (!result || result.appointment !== appointment || (result.after && !result.before)) return null;
  // Do not retain file/photo identifiers or use them to request original bytes.
  const metadata = result.before;
  const framing = includeFraming && metadata && visitPhotoText(metadata.body_area) && visitPhotoText(metadata.viewpoint)
    ? { body_area: metadata.body_area, viewpoint: metadata.viewpoint } : null;
  return { Before: metadata !== null, After: result.after !== null, framing,
    drawingsEnabled: result.drawings_enabled === true };
}

export async function readVisitPhotoImage(appointment: string, slot: VisitPhotoSlot, signal: AbortSignal) {
  if (!reference.safeParse(appointment).success) return null;
  const response = await request(`${method}read_image`, { payload: { appointment, slot } }, signal, "image/png, image/jpeg");
  if (!response) return null;
  const mime = response.headers.get("Content-Type")?.split(";")[0]?.trim().toLowerCase();
  if (mime !== "image/png" && mime !== "image/jpeg") return null;
  if (Number(response.headers.get("Content-Length")) > maxImageBytes) return null;
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length > maxImageBytes || !signatures[mime].every((byte, i) => bytes[i] === byte)) return null;
  return new Blob([bytes], { type: mime });
}

export function visitPhotoText(value: string, min = 1): boolean {
  const length = [...value].length;
  return length >= min && length <= 140 && value.trim() === value && !/[\p{C}\p{Zl}\p{Zp}]/u.test(value);
}

export async function readVisitPhotoOptions(payload: VisitPhotoTarget, signal: AbortSignal) {
  const response = await request(optionsMethod, { payload }, signal);
  return options.safeParse(await message(response)).data ?? null;
}

export async function changeVisitPhotoConsent(appointment: string, evidence: string | null) {
  const response = evidence === null
    ? await request(`${method}revoke_consent`, { payload: { appointment } })
    : await request(`${method}grant_consent`, { payload: { appointment, attested: true, evidence } });
  return consent.safeParse(await message(response)).success;
}

export async function encodeVisitPhoto(file: File) {
  const mime = file.type;
  if ((mime !== "image/png" && mime !== "image/jpeg") || !file.size || file.size > maxImageBytes) return null;
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length !== file.size || !signatures[mime].every((byte, i) => bytes[i] === byte)) return null;
  const output: string[] = [];
  // Multiple of 3 avoids internal base64 padding and bounds each conversion.
  const chunkSize = 3 * 8192;
  for (let i = 0; i < bytes.length; i += chunkSize)
    output.push(btoa(String.fromCharCode(...bytes.subarray(i, i + chunkSize))));
  return output.join("");
}

export async function captureVisitPhoto(
  payload: VisitPhotoTarget & VisitPhotoFraming & { slot: VisitPhotoSlot; request_key: string; image_base64: string },
) {
  const response = await request(`${uploadMethod}capture`, { payload });
  return staged.safeParse(await message(response)).success ? "pending" : "unknown";
}

export async function readVisitPhotoStatus(payload: { appointment: string; slot: VisitPhotoSlot; request_key: string }) {
  const response = await request(`${uploadMethod}capture_status`, { payload });
  return status.safeParse(await message(response)).data?.state ?? "unknown";
}
