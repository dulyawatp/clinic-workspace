import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Distinct locks for the two admin contract surfaces. */
const USER_LOCK = "clinic.admin-user-write.pending";
const MASTER_LOCK = "clinic.admin-master-write.pending";

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export type AdminWriteFailure =
  | "locked"
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

export class NativeAdminWriteError extends Error {
  readonly code: AdminWriteFailure;

  constructor(code: AdminWriteFailure) {
    super(code);
    this.name = "NativeAdminWriteError";
    this.code = code;
  }
}

export const CLINIC_ROLE_CODES = [
  "DOC",
  "REC",
  "NUR",
  "PHA",
  "CAS",
  "MGR",
  "ADM",
] as const;
export type ClinicRoleCode = (typeof CLINIC_ROLE_CODES)[number];

export const MASTER_TYPES = [
  "warehouse",
  "supplier",
  "item",
  "price_list",
  "mode_of_payment",
  "procedure_template",
] as const;
export type MasterType = (typeof MASTER_TYPES)[number];

export interface CreateUserInput {
  operation_key: string;
  email?: string;
  username?: string;
  full_name: string;
  clinic_role_code: ClinicRoleCode;
  practitioner?: string;
  password: string;
}

export interface EditMasterInput {
  operation_key: string;
  master_type: MasterType;
  payload: Record<string, string | number>;
}

export interface CreatedUser {
  user_id: string;
  roles: string[];
  practitioner: string | null;
}

export interface CreatedMaster {
  master_type: MasterType;
  master_id: string;
}

const listeners = new Set<() => void>();

function notifyLock(): void {
  for (const listener of listeners) listener();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function readLock(lock: string): boolean {
  try {
    return localStorage.getItem(lock) !== null;
  } catch {
    return true;
  }
}

export function adminUserWriteLocked(): boolean {
  return readLock(USER_LOCK);
}

export function adminMasterWriteLocked(): boolean {
  return readLock(MASTER_LOCK);
}

export function subscribeAdminWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(lock: string): void {
  try {
    localStorage.setItem(lock, "1");
    if (localStorage.getItem(lock) !== "1")
      throw new NativeAdminWriteError("locked");
  } catch {
    throw new NativeAdminWriteError("locked");
  }
}

function clearLock(lock: string): void {
  try {
    localStorage.removeItem(lock);
    if (readLock(lock)) throw new NativeAdminWriteError("uncertain");
  } catch {
    throw new NativeAdminWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativeAdminWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativeAdminWriteError("conflict");
    return new NativeAdminWriteError("locked");
  }
  if (status === 401) return new NativeAdminWriteError("auth");
  if (status === 403) return new NativeAdminWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativeAdminWriteError("validation");
  return new NativeAdminWriteError("uncertain");
}

async function acknowledge(
  token: string,
  kind: "admin_user" | "admin_master",
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.admin_write.ack", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      [FRAPPE_CSRF_HEADER]: token,
    },
    body: JSON.stringify({
      operation_key: operationKey,
      kind,
      ack_token: ackToken,
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (
    response.status !== 200 ||
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["acknowledged"]) ||
    body.message.acknowledged !== true
  )
    throw new NativeAdminWriteError("uncertain");
}

function parseUser(body: unknown): { result: CreatedUser; ackToken: string } {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "user", "ackToken"]) ||
    body.message.status !== "created" ||
    typeof body.message.ackToken !== "string" ||
    !ACK_TOKEN.test(body.message.ackToken) ||
    !isRecord(body.message.user) ||
    !hasExactKeys(body.message.user, ["user_id", "roles", "practitioner"])
  )
    throw new NativeAdminWriteError("uncertain");
  const user = body.message.user;
  if (
    !isText(user.user_id) ||
    !Array.isArray(user.roles) ||
    !user.roles.every((role) => isText(role)) ||
    !(user.practitioner === null || isText(user.practitioner))
  )
    throw new NativeAdminWriteError("uncertain");
  return {
    result: {
      user_id: user.user_id,
      roles: [...user.roles],
      practitioner: user.practitioner,
    },
    ackToken: body.message.ackToken,
  };
}

function parseMaster(
  body: unknown,
  expected: MasterType,
): { result: CreatedMaster; ackToken: string } {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "master", "ackToken"]) ||
    body.message.status !== "created" ||
    typeof body.message.ackToken !== "string" ||
    !ACK_TOKEN.test(body.message.ackToken) ||
    !isRecord(body.message.master) ||
    !hasExactKeys(body.message.master, ["master_type", "master_id"])
  )
    throw new NativeAdminWriteError("uncertain");
  const master = body.message.master;
  if (
    master.master_type !== expected ||
    !isText(master.master_id)
  )
    throw new NativeAdminWriteError("uncertain");
  return {
    result: {
      master_type: expected,
      master_id: master.master_id,
    },
    ackToken: body.message.ackToken,
  };
}

/**
 * One guarded POST with one explicit acknowledgement and no retry anywhere.
 * The lock is persisted before dispatch and only cleared after a confirmed ack;
 * any uncertain outcome keeps it. The password is sent once and never stored.
 */
export async function createNativeUser(
  input: CreateUserInput,
): Promise<CreatedUser> {
  if (
    !isText(input.operation_key) ||
    !isText(input.full_name) ||
    !isText(input.password) ||
    (!isText(input.email) && !isText(input.username))
  )
    throw new NativeAdminWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || adminUserWriteLocked())
    throw new NativeAdminWriteError("locked");

  persistLock(USER_LOCK);

  let response: Response;
  try {
    response = await frappeRequest("clinic_ui.api.admin_write.create_user", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify({
        operation_key: input.operation_key,
        full_name: input.full_name,
        clinic_role_code: input.clinic_role_code,
        password: input.password,
        ...(input.email ? { email: input.email } : {}),
        ...(input.username ? { username: input.username } : {}),
        ...(input.practitioner ? { practitioner: input.practitioner } : {}),
      }),
    });
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let parsed: { result: CreatedUser; ackToken: string };
  try {
    parsed = parseUser(body);
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  try {
    await acknowledge(
      token,
      "admin_user",
      input.operation_key,
      parsed.ackToken,
    );
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  clearLock(USER_LOCK);
  return parsed.result;
}

export async function editNativeMaster(
  input: EditMasterInput,
): Promise<CreatedMaster> {
  if (
    !isText(input.operation_key) ||
    !MASTER_TYPES.includes(input.master_type) ||
    !isRecord(input.payload)
  )
    throw new NativeAdminWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || adminMasterWriteLocked())
    throw new NativeAdminWriteError("locked");

  persistLock(MASTER_LOCK);

  let response: Response;
  try {
    response = await frappeRequest("clinic_ui.api.admin_write.edit_master", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify({
        operation_key: input.operation_key,
        master_type: input.master_type,
        payload: input.payload,
      }),
    });
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let parsed: { result: CreatedMaster; ackToken: string };
  try {
    parsed = parseMaster(body, input.master_type);
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  try {
    await acknowledge(
      token,
      "admin_master",
      input.operation_key,
      parsed.ackToken,
    );
  } catch {
    notifyLock();
    throw new NativeAdminWriteError("uncertain");
  }

  clearLock(MASTER_LOCK);
  return parsed.result;
}
