import { z } from "zod";
import { isRoleCode, type RoleCode } from "../../domain/capability";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { classifyNativeForbidden, nativeCsrfToken } from "./session";

export const OWNER_STAFF_PAGE_SIZE = 25;

const staffRow = z.object({
  userId: z.string().min(1),
  displayName: z.string().min(1),
  enabled: z.boolean(),
  roles: z.array(z.custom<RoleCode>(isRoleCode)),
  editable: z.boolean(),
  protectedReason: z.string().nullable(),
  onboardingStatus: z.string().nullable(),
  version: z.string().min(1),
});

const envelope = z.object({
  message: z.object({
    staff: z.array(staffRow).max(OWNER_STAFF_PAGE_SIZE),
    start: z.number().int().nonnegative(),
    pageSize: z.literal(OWNER_STAFF_PAGE_SIZE),
    hasMore: z.boolean(),
  }),
});

export type OwnerStaffPageData = z.infer<typeof envelope>["message"];

const text = z.string().min(1);
const roles = z.array(text);
const roleVersion = z.object({ userId: text, version: text });
const roleOptionsEnvelope = z.object({
  message: roleVersion.extend({
    roles,
    choices: z.array(z.object({ role: text, label: text })),
  }),
});
const roleUpdateEnvelope = z.object({
  message: roleVersion.extend({
    beforeRoles: roles,
    afterRoles: roles,
    automaticAdded: roles,
    automaticRemoved: roles,
    beforeUserType: text,
    afterUserType: text,
  }),
});
const errorEnvelope = z.object({
  code: z.string().optional(),
  message: z.object({ code: z.string() }).optional(),
});

export type OwnerStaffRoleOptions = z.infer<typeof roleOptionsEnvelope>["message"];
export type OwnerStaffRoleUpdate = z.infer<typeof roleUpdateEnvelope>["message"];
type RoleUpdateInput = z.infer<typeof roleVersion> & { roles: string[] };
type RoleUpdateResult =
  | { kind: "saved"; update: OwnerStaffRoleUpdate }
  | { kind: "denied" | "expired" | "validation" | "stale" | "uncertain" };

export async function listNativeOwnerStaff(
  start: number,
  signal: AbortSignal,
): Promise<OwnerStaffPageData> {
  const query = new URLSearchParams({
    start: String(start),
    page_size: String(OWNER_STAFF_PAGE_SIZE),
  });
  const response = await frappeRequest(
    `clinic_ui.api.owner_staff.list_staff?${query}`,
    {
      method: "GET",
      signal,
    },
  );
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (!response.ok) throw mapHttpError(response.status, null);
  const parsed = envelope.safeParse(await response.json().catch(() => null));
  if (
    !parsed.success ||
    parsed.data.message.start !== start ||
    (parsed.data.message.hasMore &&
      parsed.data.message.staff.length !== OWNER_STAFF_PAGE_SIZE)
  ) {
    throw { code: "UPSTREAM_UNAVAILABLE", retryable: true } satisfies AppError;
  }
  return parsed.data.message;
}

export async function loadNativeOwnerStaffRoleOptions(
  userId: string,
  signal: AbortSignal,
): Promise<OwnerStaffRoleOptions> {
  const query = new URLSearchParams({ user_id: userId });
  const response = await frappeRequest(
    `clinic_ui.api.owner_staff.role_options?${query}`,
    { method: "GET", signal },
  );
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (!response.ok) throw mapHttpError(response.status, null);
  const parsed = roleOptionsEnvelope.safeParse(await response.json().catch(() => null));
  if (!parsed.success || parsed.data.message.userId !== userId) {
    throw { code: "UPSTREAM_UNAVAILABLE", retryable: true } satisfies AppError;
  }
  return parsed.data.message;
}

export async function updateNativeOwnerStaffRoles(
  input: RoleUpdateInput,
): Promise<RoleUpdateResult> {
  const token = nativeCsrfToken();
  if (!token) return { kind: "expired" };

  try {
    const response = await frappeRequest("clinic_ui.api.owner_staff.update_roles", {
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
      const parsed = roleUpdateEnvelope.safeParse(body);
      if (parsed.success && parsed.data.message.userId === input.userId) {
        return { kind: "saved", update: parsed.data.message };
      }
    }
  } catch {
    // The request may have reached Frappe. Never resend a write here.
  }
  return { kind: "uncertain" };
}
