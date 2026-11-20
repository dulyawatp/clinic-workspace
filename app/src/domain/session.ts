import type { CapabilityMap, RoleCode } from "./capability";

/**
 * Session shape shared with `GET /api/session`, plus explicitly negotiated
 * native bootstrap extensions.
 */
export interface SessionDTO {
  /** Missing mode is a legacy fixture session, never live. */
  mode?: "live" | "fixture";
  /** Opaque, non-secret server-session correlation; not a cookie or credential. */
  sessionEpoch: string;
  user: {
    id: string;
    displayName: string;
    roles: RoleCode[];
  };
  capabilities: CapabilityMap;
  /** Set only by the native bootstrap mapper. */
  paymentSettlement?: "full_balance_v1";
}

const ROLE_CODES: ReadonlySet<string> = new Set([
  "REC",
  "DOC",
  "NUR",
  "PHA",
  "CAS",
  "MGR",
  "ADM",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRoleList(value: unknown): value is RoleCode[] {
  return (
    Array.isArray(value) &&
    value.every((role) => typeof role === "string" && ROLE_CODES.has(role))
  );
}

/**
 * Validate the shared session fields. Native extensions are mapped separately.
 */
export function isSessionDTO(value: unknown): value is Omit<SessionDTO, "paymentSettlement"> {
  if (!isRecord(value) || !isRecord(value.user)) {
    return false;
  }

  const user = value.user;
  return (
    (value.mode === undefined ||
      value.mode === "live" ||
      value.mode === "fixture") &&
    typeof value.sessionEpoch === "string" &&
    value.sessionEpoch.trim().length > 0 &&
    value.sessionEpoch.length <= 128 &&
    typeof user.id === "string" &&
    typeof user.displayName === "string" &&
    isRoleList(user.roles) &&
    isRecord(value.capabilities)
  );
}
