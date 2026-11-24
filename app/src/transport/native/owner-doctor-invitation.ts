import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const invitationEnvelope = z.object({
  message: z.object({
    status: z.enum(["not_queued", "queued", "sent", "failed", "unknown", "unavailable"]),
    queueId: z.string().min(1).max(256).optional(),
    practitioner: z.string().min(1).max(140).optional(),
  }),
});

export type OwnerDoctorInvitationStatus = z.infer<typeof invitationEnvelope>["message"];
export type OwnerDoctorInvitationInput = {
  practitioner: string;
  userId: string;
  previousQueueId?: string;
};
export type OwnerDoctorInvitationResult =
  | { kind: "ready"; invitation: OwnerDoctorInvitationStatus }
  | { kind: "denied" | "expired" | "validation" | "uncertain" };

export async function lookupNativeOwnerDoctorInvitation(
  { userId, practitioner }: { userId: string; practitioner?: string },
  signal: AbortSignal,
): Promise<OwnerDoctorInvitationResult> {
  try {
    const query = new URLSearchParams({ user_id: userId });
    if (practitioner !== undefined) query.set("practitioner", practitioner);
    const response = await frappeRequest(`clinic_ui.api.owner_doctor_invitation.status?${query}`, {
      method: "GET",
      signal,
    });
    if (response.status === 401) return { kind: "expired" };
    if (response.status === 403) return { kind: "denied" };
    if (!response.ok) return { kind: "uncertain" };
    const parsed = invitationEnvelope.safeParse(await response.json());
    if (parsed.success) return { kind: "ready", invitation: parsed.data.message };
  } catch {
    // Failure does not establish invitation status.
  }
  return { kind: "uncertain" };
}

export async function sendNativeOwnerDoctorInvitation(
  input: OwnerDoctorInvitationInput,
): Promise<OwnerDoctorInvitationResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };
  try {
    const response = await frappeRequest("clinic_ui.api.owner_doctor_invitation.send", {
      method: "POST",
      headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify({
        practitioner: input.practitioner,
        userId: input.userId,
        ...(input.previousQueueId === undefined ? {} : { previousQueueId: input.previousQueueId }),
      }),
    });
    if (response.status === 401) return { kind: "expired" };
    if (response.status === 403) return { kind: "denied" };
    if (response.status === 422) return { kind: "validation" };
    if (response.ok) {
      const parsed = invitationEnvelope.safeParse(await response.json());
      if (parsed.success) return { kind: "ready", invitation: parsed.data.message };
    }
  } catch {
    // Dispatch may have reached Frappe; never replay this write.
  }
  return { kind: "uncertain" };
}
