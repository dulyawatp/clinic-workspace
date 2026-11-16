import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Distinct from the patient, appointment and encounter locks. */
const LOCK = "clinic.procedure-write.pending";

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export type ProcedureWriteKind = "procedure_complete";
export type ProcedureWriteStatus = "completed";
export type ProcedureWriteFailure =
  | "locked"
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

export class NativeProcedureWriteError extends Error {
  readonly code: ProcedureWriteFailure;

  constructor(code: ProcedureWriteFailure) {
    super(code);
    this.name = "NativeProcedureWriteError";
    this.code = code;
  }
}

export interface CompleteProcedureInput {
  operation_key: string;
  /** The server-owned workspace procedure id; never a native docname. */
  procedure: string;
}

export interface NativeProcedureDetail {
  procedure_id: string;
  state: "completed";
  status: string;
}

export interface ProcedureWriteResult {
  status: ProcedureWriteStatus;
  procedure: NativeProcedureDetail;
  stockEntryCreated: boolean;
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

/** The browser lock is the only persisted write state: no data is stored. */
export function procedureWriteLocked(): boolean {
  try {
    return localStorage.getItem(LOCK) !== null;
  } catch {
    return true;
  }
}

export function subscribeProcedureWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(): void {
  try {
    localStorage.setItem(LOCK, "1");
    if (localStorage.getItem(LOCK) !== "1")
      throw new NativeProcedureWriteError("locked");
  } catch {
    throw new NativeProcedureWriteError("locked");
  }
}

function clearLock(): void {
  try {
    localStorage.removeItem(LOCK);
    if (procedureWriteLocked()) throw new NativeProcedureWriteError("uncertain");
  } catch {
    throw new NativeProcedureWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativeProcedureWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativeProcedureWriteError("conflict");
    return new NativeProcedureWriteError("locked");
  }
  if (status === 401) return new NativeProcedureWriteError("auth");
  if (status === 403) return new NativeProcedureWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativeProcedureWriteError("validation");
  return new NativeProcedureWriteError("uncertain");
}

function parseDetail(value: unknown, expectedAlias: string): NativeProcedureDetail {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["procedure_id", "state", "status"]) ||
    value.procedure_id !== expectedAlias ||
    value.state !== "completed" ||
    !isText(value.status)
  )
    throw new NativeProcedureWriteError("uncertain");
  return {
    procedure_id: expectedAlias,
    state: "completed",
    status: value.status,
  };
}

function parseWriteResult(
  expectedAlias: string,
  body: unknown,
): { result: ProcedureWriteResult; ackToken: string } {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, [
      "status",
      "procedure",
      "ackToken",
      "stockEntryCreated",
    ])
  )
    throw new NativeProcedureWriteError("uncertain");

  const message = body.message;
  if (
    message.status !== "completed" ||
    typeof message.ackToken !== "string" ||
    !ACK_TOKEN.test(message.ackToken) ||
    typeof message.stockEntryCreated !== "boolean"
  )
    throw new NativeProcedureWriteError("uncertain");

  return {
    result: {
      status: "completed",
      procedure: parseDetail(message.procedure, expectedAlias),
      stockEntryCreated: message.stockEntryCreated,
    },
    ackToken: message.ackToken,
  };
}

async function acknowledge(
  token: string,
  kind: ProcedureWriteKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.procedures_write.ack", {
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
    throw new NativeProcedureWriteError("uncertain");
}

/**
 * One guarded POST with one explicit acknowledgement and no retry anywhere.
 * The lock is persisted before dispatch and only cleared after a confirmed ack;
 * any uncertain outcome keeps it, so a reload shows the stop state again.
 */
export async function completeNativeProcedure(
  input: CompleteProcedureInput,
): Promise<ProcedureWriteResult> {
  if (!isText(input.operation_key) || !isText(input.procedure))
    throw new NativeProcedureWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || procedureWriteLocked())
    throw new NativeProcedureWriteError("locked");

  persistLock();

  let response: Response;
  try {
    response = await frappeRequest(
      "clinic_ui.api.procedures_write.complete",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          [FRAPPE_CSRF_HEADER]: token,
        },
        body: JSON.stringify({
          operation_key: input.operation_key,
          procedure: input.procedure,
        }),
      },
    );
  } catch {
    notifyLock();
    throw new NativeProcedureWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let parsed: { result: ProcedureWriteResult; ackToken: string };
  try {
    parsed = parseWriteResult(input.procedure, body);
  } catch {
    notifyLock();
    throw new NativeProcedureWriteError("uncertain");
  }

  try {
    await acknowledge(
      token,
      "procedure_complete",
      input.operation_key,
      parsed.ackToken,
    );
  } catch {
    notifyLock();
    throw new NativeProcedureWriteError("uncertain");
  }

  clearLock();
  return parsed.result;
}
