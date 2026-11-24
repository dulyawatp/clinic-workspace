import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const text = z.string().min(1);
const option = z.object({ name: text, displayName: text });
const practitioner = option.extend({ version: text });
const lookupEnvelope = z.object({
  message: z.object({
    departments: z.array(option),
    practitioners: z.array(practitioner),
    canCreate: z.boolean().optional(),
  }),
});
const createEnvelope = z.object({ message: practitioner });

export type OwnerDoctorProfile = z.infer<typeof practitioner>;
export type OwnerDoctorProfileOptions = z.infer<typeof lookupEnvelope>["message"];
type ProfileInput = { firstName: string; lastName: string; department: string };
export type OwnerDoctorProfileResult =
  | { kind: "created"; profile: OwnerDoctorProfile }
  | { kind: "denied" | "expired" | "validation" | "uncertain" };

export async function lookupNativeOwnerDoctorProfiles(
  signal: AbortSignal,
): Promise<OwnerDoctorProfileOptions | null> {
  const response = await frappeRequest("clinic_ui.api.owner_doctor_profile.lookup", {
    method: "GET",
    signal,
  });
  if (!response.ok) return null;
  const parsed = lookupEnvelope.safeParse(await response.json());
  return parsed.success ? parsed.data.message : null;
}

export async function createNativeOwnerDoctorProfile(
  input: ProfileInput,
): Promise<OwnerDoctorProfileResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };

  try {
    const response = await frappeRequest("clinic_ui.api.owner_doctor_profile.create", {
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
    if (response.ok) {
      const parsed = createEnvelope.safeParse(await response.json());
      if (parsed.success) return { kind: "created", profile: parsed.data.message };
    }
  } catch {
    // The request may have reached Frappe. Do not replay the write.
  }
  return { kind: "uncertain" };
}
