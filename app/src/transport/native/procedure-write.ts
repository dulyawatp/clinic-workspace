import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Distinct from the patient, appointment and encounter locks. */
const LOCK = "clinic.procedure-write.pending";
/** The creation path keeps its own lock: an uncertain create never blocks a
 * completion, and neither is ever unlocked automatically. */
const CREATE_LOCK = "clinic.procedure-create-write.pending";

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export type ProcedureWriteKind = "procedure_complete" | "procedure_create";
export type ProcedureWriteStatus = "completed";
export type ProcedureCreateStatus = "created";
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

export interface CreateProcedureInput {
  operation_key: string;
  /** The Service Request the actor selected; the created row is server-built. */
  service_request: string;
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

export interface NativeProcedureCreated {
  procedure_id: string;
  state: "pending";
  status: string;
}

export interface ProcedureCreateResult {
  status: ProcedureCreateStatus;
  procedure: NativeProcedureCreated;
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

/** The browser lock is the only persisted write state: no data is stored. */
export function procedureWriteLocked(): boolean {
  return readLock(LOCK);
}

export function procedureCreateLocked(): boolean {
  return readLock(CREATE_LOCK);
}

export function subscribeProcedureWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(lock: string): void {
  try {
    localStorage.setItem(lock, "1");
    if (localStorage.getItem(lock) !== "1")
      throw new NativeProcedureWriteError("locked");
  } catch {
    throw new NativeProcedureWriteError("locked");
  }
}

function clearLock(lock: string): void {
  try {
    localStorage.removeItem(lock);
    if (readLock(lock)) throw new NativeProcedureWriteError("uncertain");
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

  persistLock(LOCK);

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

  clearLock(LOCK);
  return parsed.result;
}

function parseCreateResult(body: unknown): {
  result: ProcedureCreateResult;
  ackToken: string;
} {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "procedure", "ackToken"])
  )
    throw new NativeProcedureWriteError("uncertain");

  const message = body.message;
  const procedure = message.procedure;
  if (
    message.status !== "created" ||
    typeof message.ackToken !== "string" ||
    !ACK_TOKEN.test(message.ackToken) ||
    !isRecord(procedure) ||
    !hasExactKeys(procedure, ["procedure_id", "state", "status"]) ||
    !isText(procedure.procedure_id) ||
    procedure.state !== "pending" ||
    !isText(procedure.status)
  )
    throw new NativeProcedureWriteError("uncertain");

  return {
    result: {
      status: "created",
      procedure: {
        procedure_id: procedure.procedure_id,
        state: "pending",
        status: procedure.status,
      },
    },
    ackToken: message.ackToken,
  };
}

/**
 * One guarded creation POST with one explicit acknowledgement and no retry.
 *
 * The request carries the selected Service Request and an operation key only.
 * The returned procedure id is the workspace alias the completion path resolves
 * through the committed creation receipt, so no native docname reaches the
 * browser. The creation lock is persisted before dispatch and only cleared
 * after a confirmed ack.
 */
export async function createNativeProcedure(
  input: CreateProcedureInput,
): Promise<ProcedureCreateResult> {
  if (!isText(input.operation_key) || !isText(input.service_request))
    throw new NativeProcedureWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || procedureCreateLocked())
    throw new NativeProcedureWriteError("locked");

  persistLock(CREATE_LOCK);

  let response: Response;
  try {
    response = await frappeRequest(
      "clinic_ui.api.procedures_write.create",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          [FRAPPE_CSRF_HEADER]: token,
        },
        body: JSON.stringify({
          operation_key: input.operation_key,
          service_request: input.service_request,
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

  let parsed: { result: ProcedureCreateResult; ackToken: string };
  try {
    parsed = parseCreateResult(body);
  } catch {
    notifyLock();
    throw new NativeProcedureWriteError("uncertain");
  }

  try {
    await acknowledge(
      token,
      "procedure_create",
      input.operation_key,
      parsed.ackToken,
    );
  } catch {
    notifyLock();
    throw new NativeProcedureWriteError("uncertain");
  }

  clearLock(CREATE_LOCK);
  return parsed.result;
}
