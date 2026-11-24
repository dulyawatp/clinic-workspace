import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { OWNER_HOURS_DAYS, OWNER_HOURS_MAX_ROWS } from "./owner-doctor-hours";
import { nativeCsrfToken } from "./session";

const text = z.string().min(1).max(256);
const slot = z.object({
  day: z.enum(OWNER_HOURS_DAYS),
  from_time: text,
  to_time: text,
  duration: z.number().positive(),
  maximum_appointments: z.number().int().positive(),
});
const slots = z.array(slot).min(1).max(OWNER_HOURS_MAX_ROWS);
const review = z.object({
  practitioner: text,
  practitionerVersion: text,
  schedule: text,
  scheduleVersion: text,
  serviceUnit: text,
  firstName: text,
  lastName: z.string().max(256),
  slotDigest: z.string().regex(/^[0-9a-f]{64}$/),
});
const draftReview = review.extend({ slots, mode: z.undefined().optional() });
const nativeTime = z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/);
const nativeSlots = z.array(z.object({
  day: z.enum(OWNER_HOURS_DAYS),
  from_time: nativeTime,
  to_time: nativeTime,
  duration: z.number().positive(),
  maximum_appointments: z.number().int().nonnegative(),
}).refine(({ from_time, to_time }) => from_time < to_time)).min(1).max(512);
const reuseReview = review.extend({ slots: nativeSlots, mode: z.literal("reuse") });
const reuseChoices = z.object({
  practitioner: text,
  practitionerVersion: text,
  schedules: z.array(z.object({ name: text })),
  serviceUnits: z.array(z.object({ name: text })),
});
const receipt = z.object({
  practitioner: text,
  userId: text,
  schedule: text,
  serviceUnit: text,
  slots,
  enabled: z.literal(true),
});
const draftReceiptEnvelope = z.object({ message: receipt });
const reuseReceiptEnvelope = z.object({ message: receipt.extend({ slots: nativeSlots }) });
const errorEnvelope = z.object({
  code: z.string().optional(),
  message: z.object({ code: z.string() }).optional(),
});

export type OwnerDoctorFinishOptions = z.infer<typeof draftReview> | z.infer<typeof reuseReview>;
export type OwnerDoctorReuseChoices = z.infer<typeof reuseChoices>;
export type OwnerDoctorReuseSelection = { schedule: string; serviceUnit: string };
export type OwnerDoctorFinishInput = Pick<
  z.infer<typeof review>,
  "practitioner" | "practitionerVersion" | "schedule" | "scheduleVersion" | "slotDigest"
> & { email: string } & ({ mode?: undefined } | { mode: "reuse"; serviceUnit: string });
export type OwnerDoctorFinishLookup =
  | { kind: "ready"; options: OwnerDoctorFinishOptions }
  | { kind: "unavailable" | "expired" | "error" };
export type OwnerDoctorFinishResult =
  | { kind: "finished"; receipt: z.infer<typeof draftReceiptEnvelope | typeof reuseReceiptEnvelope>["message"] }
  | { kind: "denied" | "expired" | "validation" | "stale" | "email_in_use" | "uncertain" };

export function lookupNativeOwnerDoctorFinish(
  practitioner: string,
  signal: AbortSignal,
): Promise<OwnerDoctorFinishLookup> {
  return readReview(practitioner, signal);
}

export async function lookupNativeOwnerDoctorReuseChoices(
  practitioner: string,
  signal: AbortSignal,
): Promise<OwnerDoctorReuseChoices | null> {
  try {
    const query = new URLSearchParams({ practitioner });
    const response = await frappeRequest(`clinic_ui.api.owner_doctor_finish.reuse_options?${query}`, { method: "GET", signal });
    if (!response.ok) return null;
    const parsed = z.object({ message: reuseChoices }).safeParse(await response.json());
    if (parsed.success && parsed.data.message.practitioner === practitioner) return parsed.data.message;
  } catch {
    // An unavailable read must not enable schedule reuse.
  }
  return null;
}

export function lookupNativeOwnerDoctorReuseReview(
  practitioner: string,
  selection: OwnerDoctorReuseSelection,
  signal: AbortSignal,
): Promise<OwnerDoctorFinishLookup> {
  return readReview(practitioner, signal, selection);
}

async function readReview(
  practitioner: string,
  signal: AbortSignal,
  selection?: OwnerDoctorReuseSelection,
): Promise<OwnerDoctorFinishLookup> {
  try {
    const query = new URLSearchParams({ practitioner });
    if (selection) {
      query.set("schedule", selection.schedule);
      query.set("service_unit", selection.serviceUnit);
    }
    const method = selection ? "reuse_review" : "options";
    const response = await frappeRequest(`clinic_ui.api.owner_doctor_finish.${method}?${query}`, {
      method: "GET",
      signal,
    });
    if ([403, 404, 405].includes(response.status)) return { kind: "unavailable" };
    if (response.status === 401) return { kind: "expired" };
    if (!response.ok) return { kind: "error" };
    const parsed = z.object({ message: selection ? reuseReview : draftReview }).safeParse(await response.json());
    if (parsed.success && parsed.data.message.practitioner === practitioner &&
        (!selection || (parsed.data.message.schedule === selection.schedule && parsed.data.message.serviceUnit === selection.serviceUnit))) {
      return { kind: "ready", options: parsed.data.message };
    }
  } catch {
    // Read failure is not evidence that finish is available.
  }
  return { kind: "error" };
}

export async function finishNativeOwnerDoctor(
  input: OwnerDoctorFinishInput,
): Promise<OwnerDoctorFinishResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };
  try {
    const response = await frappeRequest("clinic_ui.api.owner_doctor_finish.finish", {
      method: "POST",
      headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify(input),
    });
    if (response.status === 401) return { kind: "expired" };
    if (response.status === 403) return { kind: "denied" };
    if (response.status === 422) return { kind: "validation" };
    const body: unknown = await response.json();
    if (response.status === 409) {
      const parsed = errorEnvelope.safeParse(body);
      const code = parsed.success && (parsed.data.message?.code ?? parsed.data.code);
      if (code === "STALE_VERSION") return { kind: "stale" };
      if (code === "EMAIL_IN_USE") return { kind: "email_in_use" };
      return { kind: "uncertain" };
    }
    if (response.ok) {
      const parsed = (input.mode === "reuse" ? reuseReceiptEnvelope : draftReceiptEnvelope).safeParse(body);
      if (parsed.success && parsed.data.message.practitioner === input.practitioner &&
          (input.mode !== "reuse" || (parsed.data.message.schedule === input.schedule && parsed.data.message.serviceUnit === input.serviceUnit))) {
        return { kind: "finished", receipt: parsed.data.message };
      }
    }
  } catch {
    // Dispatch may have reached Frappe; never replay this write.
  }
  return { kind: "uncertain" };
}
