import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Distinct from the patient and appointment locks. */
const LOCK = "clinic.encounter-write.pending";

const ACK_TOKEN = /^[0-9a-f]{64}$/;
const ALIAS = /^wse1_[0-9a-f]{32}_[0-9a-f]{16}_[A-Za-z0-9_-]{16,128}$/;

export type EncounterWriteKind =
  | "encounter_draft"
  | "encounter_complete"
  | "encounter_amend";
export type EncounterWriteStatus = "draft_saved" | "completed" | "amended";
export type EncounterWriteFailure =
  | "locked"
  | "uncertain"
  | "stale"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

export class NativeEncounterWriteError extends Error {
  readonly code: EncounterWriteFailure;

  constructor(code: EncounterWriteFailure) {
    super(code);
    this.name = "NativeEncounterWriteError";
    this.code = code;
  }
}

export interface EncounterDiagnosisRow {
  diagnosis: string;
}

export interface EncounterMedicationRow {
  drug_code: string;
  dosage?: string | null;
  period?: string | null;
}

export interface EncounterLabRow {
  lab_test_code: string;
}

export interface EncounterProcedureRow {
  procedure: string;
}

export type EncounterVitals = Partial<
  Record<
    | "temperature"
    | "pulse"
    | "respiratory_rate"
    | "bp_systolic"
    | "bp_diastolic"
    | "height"
    | "weight",
    string
  >
>;

export interface EncounterNote {
  assessment?: string | null;
  plan?: string | null;
  rationale?: string | null;
  followup?: string | null;
}

export interface SaveEncounterDraftInput {
  operation_key: string;
  patient: string;
  encounter?: string | null;
  base_modified?: string | null;
  appointment?: string | null;
  complaint?: string | null;
  diagnoses?: EncounterDiagnosisRow[];
  medications?: EncounterMedicationRow[];
  lab_tests?: EncounterLabRow[];
  procedures?: EncounterProcedureRow[];
  vitals?: EncounterVitals;
  note?: EncounterNote;
  allergy_reviewed?: boolean;
}

export interface CompleteEncounterInput {
  operation_key: string;
  encounter: string;
  allergy_reviewed: boolean;
}

export interface AmendEncounterInput {
  operation_key: string;
  encounter: string;
  addendum: string;
}

export interface NativeEncounterDetail {
  encounter_id: string;
  workspace_patient_id: string;
  state: "draft" | "completed";
  status: string;
  modified: string;
}

export interface EncounterWriteResult {
  status: EncounterWriteStatus;
  encounter: NativeEncounterDetail;
  addendum?: string;
}

const STATUS_BY_KIND: Readonly<Record<EncounterWriteKind, EncounterWriteStatus>> =
  {
    encounter_draft: "draft_saved",
    encounter_complete: "completed",
    encounter_amend: "amended",
  };

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
export function encounterWriteLocked(): boolean {
  try {
    return localStorage.getItem(LOCK) !== null;
  } catch {
    return true;
  }
}

export function subscribeEncounterWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(): void {
  try {
    localStorage.setItem(LOCK, "1");
    if (localStorage.getItem(LOCK) !== "1")
      throw new NativeEncounterWriteError("locked");
  } catch {
    throw new NativeEncounterWriteError("locked");
  }
}

function clearLock(): void {
  try {
    localStorage.removeItem(LOCK);
    if (encounterWriteLocked()) throw new NativeEncounterWriteError("uncertain");
  } catch {
    throw new NativeEncounterWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativeEncounterWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "STALE_VERSION")
      return new NativeEncounterWriteError("stale");
    if (code === "OPERATION_CONFLICT")
      return new NativeEncounterWriteError("conflict");
    return new NativeEncounterWriteError("locked");
  }
  if (status === 401) return new NativeEncounterWriteError("auth");
  if (status === 403) return new NativeEncounterWriteError("forbidden");
  if (status === 422) return new NativeEncounterWriteError("validation");
  return new NativeEncounterWriteError("uncertain");
}

function parseDetail(value: unknown): NativeEncounterDetail {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "encounter_id",
      "workspace_patient_id",
      "state",
      "status",
      "modified",
    ]) ||
    typeof value.encounter_id !== "string" ||
    !ALIAS.test(value.encounter_id) ||
    !isText(value.workspace_patient_id) ||
    (value.state !== "draft" && value.state !== "completed") ||
    !isText(value.status) ||
    !isText(value.modified)
  )
    throw new NativeEncounterWriteError("uncertain");
  return {
    encounter_id: value.encounter_id,
    workspace_patient_id: value.workspace_patient_id,
    state: value.state,
    status: value.status,
    modified: value.modified,
  };
}

function parseWriteResult(
  kind: EncounterWriteKind,
  expectedAlias: string | undefined,
  body: unknown,
): EncounterWriteResult {
  const keys = kind === "encounter_amend"
    ? ["status", "encounter", "ackToken", "addendum"]
    : ["status", "encounter", "ackToken"];
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, keys)
  )
    throw new NativeEncounterWriteError("uncertain");

  const message = body.message;
  if (
    message.status !== STATUS_BY_KIND[kind] ||
    typeof message.ackToken !== "string" ||
    !ACK_TOKEN.test(message.ackToken)
  )
    throw new NativeEncounterWriteError("uncertain");

  const encounter = parseDetail(message.encounter);
  if (expectedAlias !== undefined && encounter.encounter_id !== expectedAlias)
    throw new NativeEncounterWriteError("uncertain");
  if (kind === "encounter_complete" && encounter.state !== "completed")
    throw new NativeEncounterWriteError("uncertain");

  const result: EncounterWriteResult = {
    status: STATUS_BY_KIND[kind],
    encounter,
  };
  if (kind === "encounter_amend") {
    if (!isText(message.addendum))
      throw new NativeEncounterWriteError("uncertain");
    result.addendum = message.addendum;
  }
  return result;
}

async function acknowledge(
  token: string,
  kind: EncounterWriteKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.encounters_write.ack", {
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
    throw new NativeEncounterWriteError("uncertain");
}

function validDraftInput(input: SaveEncounterDraftInput): void {
  const hasEncounter = input.encounter !== undefined && input.encounter !== null;
  if (
    !isText(input.operation_key) ||
    !isText(input.patient) ||
    (hasEncounter && !ALIAS.test(input.encounter as string)) ||
    (hasEncounter && !isText(input.base_modified)) ||
    (!hasEncounter && !isText(input.appointment))
  )
    throw new NativeEncounterWriteError("validation");
  for (const row of input.medications ?? []) {
    if (!isText(row.drug_code))
      throw new NativeEncounterWriteError("validation");
  }
}

async function writeEncounter(
  kind: EncounterWriteKind,
  method: string,
  operationKey: string,
  expectedAlias: string | undefined,
  payload: Record<string, unknown>,
): Promise<EncounterWriteResult> {
  const token = nativeCsrfToken();
  if (!token || encounterWriteLocked())
    throw new NativeEncounterWriteError("locked");

  persistLock();

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
    notifyLock();
    throw new NativeEncounterWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let result: EncounterWriteResult;
  try {
    result = parseWriteResult(kind, expectedAlias, body);
  } catch {
    notifyLock();
    throw new NativeEncounterWriteError("uncertain");
  }

  const ackToken =
    isRecord(body) && isRecord(body.message) ? body.message.ackToken : null;
  if (typeof ackToken !== "string")
    throw new NativeEncounterWriteError("uncertain");

  try {
    await acknowledge(token, kind, operationKey, ackToken);
  } catch {
    notifyLock();
    throw new NativeEncounterWriteError("uncertain");
  }

  clearLock();
  return result;
}

export async function saveNativeEncounterDraft(
  input: SaveEncounterDraftInput,
): Promise<EncounterWriteResult> {
  validDraftInput(input);
  const payload: Record<string, unknown> = {
    operation_key: input.operation_key,
    patient: input.patient,
  };
  if (input.encounter) {
    payload.encounter = input.encounter;
    payload.base_modified = input.base_modified ?? null;
  } else {
    payload.appointment = input.appointment ?? null;
  }
  for (const key of [
    "complaint",
    "diagnoses",
    "medications",
    "lab_tests",
    "procedures",
    "vitals",
    "note",
  ] as const) {
    const value = input[key];
    if (value !== undefined && value !== null) payload[key] = value;
  }
  if (input.allergy_reviewed !== undefined)
    payload.allergy_reviewed = input.allergy_reviewed;
  return writeEncounter(
    "encounter_draft",
    "clinic_ui.api.encounters_write.save_draft",
    input.operation_key,
    input.encounter ?? undefined,
    payload,
  );
}

export async function completeNativeEncounter(
  input: CompleteEncounterInput,
): Promise<EncounterWriteResult> {
  if (!isText(input.operation_key) || !ALIAS.test(input.encounter ?? ""))
    throw new NativeEncounterWriteError("validation");
  return writeEncounter(
    "encounter_complete",
    "clinic_ui.api.encounters_write.complete",
    input.operation_key,
    input.encounter,
    {
      operation_key: input.operation_key,
      encounter: input.encounter,
      allergy_reviewed: input.allergy_reviewed === true,
    },
  );
}

export async function amendNativeEncounter(
  input: AmendEncounterInput,
): Promise<EncounterWriteResult> {
  if (
    !isText(input.operation_key) ||
    !ALIAS.test(input.encounter ?? "") ||
    !isText(input.addendum) ||
    input.addendum.length > 5000
  )
    throw new NativeEncounterWriteError("validation");
  return writeEncounter(
    "encounter_amend",
    "clinic_ui.api.encounters_write.amend",
    input.operation_key,
    input.encounter,
    {
      operation_key: input.operation_key,
      encounter: input.encounter,
      addendum: input.addendum,
    },
  );
}
