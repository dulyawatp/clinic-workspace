import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type ProcedureWriteKind = "procedure_complete" | "procedure_create";
export type ProcedureWriteStatus = "completed";
export type ProcedureCreateStatus = "created";

/**
 * A deterministic refusal (`conflict`, `validation`, `auth`, `forbidden`) or an
 * unresolved outcome (`uncertain`). The converted lane reports a lost or
 * unreadable response, and a server `WRITE_UNCERTAIN`, as uncertain.
 */
export type ProcedureWriteFailure =
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden";

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
  /** The workspace procedure id or the native docname the actor holds. */
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
  /** The native docname of the row the server created. */
  procedure_id: string;
  state: "pending";
  status: string;
}

export interface ProcedureCreateResult {
  status: ProcedureCreateStatus;
  procedure: NativeProcedureCreated;
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

function errorCode(body: unknown): string | null {
  if (!isRecord(body)) return null;
  if (isRecord(body.message) && typeof body.message.code === "string")
    return body.message.code;
  return typeof body.code === "string" ? body.code : null;
}

function failure(status: number, body: unknown): NativeProcedureWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativeProcedureWriteError("conflict");
    // `WRITE_UNCERTAIN` is the server's own rolled-back outcome: still unknown.
    return new NativeProcedureWriteError("uncertain");
  }
  if (status === 401) return new NativeProcedureWriteError("auth");
  if (status === 403) return new NativeProcedureWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativeProcedureWriteError("validation");
  return new NativeProcedureWriteError("uncertain");
}

/**
 * Validates the exact `{status, procedure, stockEntryCreated}` completion
 * result. The echoed detail must be the committed target this request named and
 * must read back as completed; an unexpected envelope field (including the
 * retired `ackToken`) fails as uncertain. The browser never derives an id of
 * its own and never claims a state it was not handed.
 */
function parseCompleteResult(
  expectedId: string,
  body: unknown,
): ProcedureWriteResult {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "procedure", "stockEntryCreated"])
  )
    throw new NativeProcedureWriteError("uncertain");

  const message = body.message;
  if (
    message.status !== "completed" ||
    typeof message.stockEntryCreated !== "boolean" ||
    !isRecord(message.procedure) ||
    !hasExactKeys(message.procedure, ["procedure_id", "state", "status"]) ||
    message.procedure.procedure_id !== expectedId ||
    message.procedure.state !== "completed" ||
    !isText(message.procedure.status)
  )
    throw new NativeProcedureWriteError("uncertain");

  return {
    status: "completed",
    procedure: {
      procedure_id: expectedId,
      state: "completed",
      status: message.procedure.status,
    },
    stockEntryCreated: message.stockEntryCreated,
  };
}

/**
 * Validates the exact `{status, procedure}` creation result. Creation returns
 * the native docname of the row the server created; the client validates the
 * shape and never composes or rewrites that identifier.
 */
function parseCreateResult(body: unknown): ProcedureCreateResult {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "procedure"])
  )
    throw new NativeProcedureWriteError("uncertain");

  const message = body.message;
  if (message.status !== "created") {
    throw new NativeProcedureWriteError("uncertain");
  }
  const procedure = message.procedure;
  if (
    !isRecord(procedure) ||
    !hasExactKeys(procedure, ["procedure_id", "state", "status"]) ||
    !isText(procedure.procedure_id) ||
    procedure.state !== "pending" ||
    !isText(procedure.status)
  )
    throw new NativeProcedureWriteError("uncertain");

  return {
    status: "created",
    procedure: {
      procedure_id: procedure.procedure_id,
      state: "pending",
      status: procedure.status,
    },
  };
}

/**
 * One procedure POST through the native Frappe method.
 *
 * The backend owns validation, permissions, the transaction and the canonical
 * result, so the returned detail is validated by shape and never compared
 * field-by-field with what the form submitted. There is no acknowledgement
 * request and no browser-side fence: the caller keeps the in-flight guard,
 * renders a deterministic refusal as a known failure, and must not retry an
 * unknown outcome before the records are checked. Every path throws at most
 * once and nothing is retried here.
 */
async function writeProcedure<T>(
  method: string,
  payload: Record<string, string>,
  parse: (body: unknown) => T,
): Promise<T> {
  const token = nativeCsrfToken();
  if (!token) throw new NativeProcedureWriteError("uncertain");

  let response: Response;
  try {
    response = await frappeRequest(method, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(payload),
    });
  } catch {
    // The request may have reached the server: the outcome is unknown.
    throw new NativeProcedureWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);

  return parse(body);
}

/** Complete one resolved procedure; the server performs the stock movement. */
export async function completeNativeProcedure(
  input: CompleteProcedureInput,
): Promise<ProcedureWriteResult> {
  if (!isText(input.operation_key) || !isText(input.procedure))
    throw new NativeProcedureWriteError("validation");

  return writeProcedure(
    "clinic_ui.api.procedures_write.complete",
    {
      operation_key: input.operation_key,
      procedure: input.procedure,
    },
    (body) => parseCompleteResult(input.procedure, body),
  );
}

/** Create one procedure from a submitted Service Request. */
export async function createNativeProcedure(
  input: CreateProcedureInput,
): Promise<ProcedureCreateResult> {
  if (!isText(input.operation_key) || !isText(input.service_request))
    throw new NativeProcedureWriteError("validation");

  return writeProcedure(
    "clinic_ui.api.procedures_write.create",
    {
      operation_key: input.operation_key,
      service_request: input.service_request,
    },
    parseCreateResult,
  );
}
