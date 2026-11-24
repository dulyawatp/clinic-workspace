import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export const OWNER_HOURS_DAYS = [
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
] as const;
export const OWNER_HOURS_MAX_ROWS = 56;
export const OWNER_HOURS_LOOKUP_LIMIT = 100;

const text = z.string().min(1).max(140);
const lookupEnvelope = z.object({
  message: z.object({
    practitioners: z.array(z.object({ name: text, version: text })).max(OWNER_HOURS_LOOKUP_LIMIT),
    serviceUnits: z.array(z.object({ name: text })).max(OWNER_HOURS_LOOKUP_LIMIT),
  }),
});
const savedEnvelope = z.object({
  message: z.object({ practitioner: text, version: text, schedule: text }),
});
const errorEnvelope = z.object({
  code: z.string().optional(),
  message: z.object({ code: z.string() }).optional(),
});

export type OwnerHoursLookup =
  | { kind: "ready"; options: z.infer<typeof lookupEnvelope>["message"] }
  | { kind: "unavailable" | "expired" | "error" };

export type OwnerHoursSlot = {
  day: (typeof OWNER_HOURS_DAYS)[number];
  from_time: string;
  to_time: string;
  duration: number;
  maximum_appointments: number;
};

type OwnerHoursDraft = {
  practitioner: string;
  version: string;
  serviceUnit: string;
  slots: OwnerHoursSlot[];
};

export type OwnerHoursSaveResult =
  | { kind: "saved"; receipt: z.infer<typeof savedEnvelope>["message"] }
  | { kind: "denied" | "expired" | "validation" | "stale" | "uncertain" };

export async function lookupNativeOwnerHours(signal: AbortSignal): Promise<OwnerHoursLookup> {
  const response = await frappeRequest("clinic_ui.api.owner_doctor_hours.lookup", {
    method: "GET",
    signal,
  });
  // Off and forbidden have the same response. Do not expose an editor for either.
  if ([403, 404, 405].includes(response.status)) return { kind: "unavailable" };
  if (response.status === 401) return { kind: "expired" };
  if (!response.ok) return { kind: "error" };
  const parsed = lookupEnvelope.safeParse(await response.json().catch(() => null));
  return parsed.success
    ? { kind: "ready", options: parsed.data.message }
    : { kind: "error" };
}

export async function stageNativeOwnerHours(input: OwnerHoursDraft): Promise<OwnerHoursSaveResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };

  try {
    const response = await frappeRequest("clinic_ui.api.owner_doctor_hours.stage", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(input),
    });
    if (response.status === 401) return { kind: "expired" };
    if (response.status === 403) return { kind: "denied" };
    if (response.status === 422) return { kind: "validation" };

    const body: unknown = await response.json().catch(() => null);
    if (response.status === 409) {
      const parsed = errorEnvelope.safeParse(body);
      const code = parsed.success && (parsed.data.message?.code ?? parsed.data.code);
      return { kind: code === "STALE_VERSION" ? "stale" : "uncertain" };
    }
    if (response.ok) {
      const parsed = savedEnvelope.safeParse(body);
      if (parsed.success && parsed.data.message.practitioner === input.practitioner) {
        return { kind: "saved", receipt: parsed.data.message };
      }
    }
  } catch {
    // Dispatch may have reached Frappe; never replay this write.
  }
  return { kind: "uncertain" };
}
