import { z } from "zod";
import { frappeRequest } from "./frappe";

const weekday = z.enum([
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
]).transform((day) => day.slice(0, 3).toLowerCase());

const clockTime = z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/)
  .transform((time) => time.length === 8 && time.endsWith(":00") ? time.slice(0, 5) : time);

const envelope = z.object({
  message: z.discriminatedUnion("state", [
    z.object({
      state: z.literal("ready"),
      schedules: z.array(z.object({
        slots: z.array(z.object({
          day: weekday,
          from_time: clockTime,
          to_time: clockTime,
        })).min(1),
        serviceUnit: z.string().trim().min(1).optional(),
      })).min(1),
    }),
    z.object({ state: z.literal("empty") }),
    z.object({ state: z.literal("unavailable") }),
  ]),
});

export type DoctorHours = z.infer<typeof envelope>["message"];

export async function readNativeDoctorHours(signal: AbortSignal): Promise<DoctorHours> {
  try {
    const response = await frappeRequest("clinic_ui.api.doctor_hours.get", {
      method: "GET",
      signal,
    });
    if (response.ok) {
      const parsed = envelope.safeParse(await response.json());
      if (parsed.success) return parsed.data.message;
    }
  } catch {
    // The caller ignores aborted reads. Never display upstream error details.
  }
  return { state: "unavailable" };
}
