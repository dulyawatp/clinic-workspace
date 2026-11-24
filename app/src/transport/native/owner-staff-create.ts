import { z } from "zod";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const text = z.string().min(1);
const choicesEnvelope = z.object({
  message: z.object({
    choices: z.array(z.object({ role: text, label: text })),
  }),
});
const createdEnvelope = z.object({
  message: z.object({
    userId: text,
    email: text,
    enabled: z.literal(true),
    roles: z.array(text),
  }),
});
const errorEnvelope = z.object({
  code: z.string().optional(),
  message: z.object({ code: z.string() }).optional(),
});

export type OwnerStaffCreateChoices = z.infer<typeof choicesEnvelope>["message"]["choices"];
export type OwnerStaffCreateInput = {
  email: string;
  firstName: string;
  lastName: string;
  roles: string[];
};
export type OwnerStaffCreateResult =
  | { kind: "created"; account: z.infer<typeof createdEnvelope>["message"] }
  | { kind: "duplicate" | "denied" | "expired" | "validation" | "uncertain" };

export async function loadNativeOwnerStaffCreateChoices(
  signal: AbortSignal,
): Promise<OwnerStaffCreateChoices> {
  const response = await frappeRequest("clinic_ui.api.owner_staff.create_options", {
    method: "GET",
    signal,
  });
  if (!response.ok) return [];
  const parsed = choicesEnvelope.safeParse(await response.json());
  return parsed.success ? parsed.data.message.choices : [];
}

export async function createNativeOwnerStaff(
  input: OwnerStaffCreateInput,
): Promise<OwnerStaffCreateResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };

  try {
    const response = await frappeRequest("clinic_ui.api.owner_staff.create_staff", {
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

    const body: unknown = await response.json();
    if (response.status === 409) {
      const parsed = errorEnvelope.safeParse(body);
      const code = parsed.success && (parsed.data.message?.code ?? parsed.data.code);
      return { kind: code === "EMAIL_IN_USE" ? "duplicate" : "uncertain" };
    }
    if (response.ok) {
      const parsed = createdEnvelope.safeParse(body);
      if (parsed.success && parsed.data.message.email.toLowerCase() === input.email.toLowerCase()) {
        return { kind: "created", account: parsed.data.message };
      }
    }
  } catch {
    // A failed or stale response cannot establish whether Frappe created the user.
  }
  return { kind: "uncertain" };
}
