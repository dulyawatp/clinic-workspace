import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { fetchNativePatientDetailBody } from "../../transport/native/patient";

/**
 * The one patient detail shape the native read exposes. It mirrors
 * `clinic_ui.core.dto.patient_detail`: display fields only, never a native
 * docname, contact value, identifier or clinical field.
 */
export interface LivePatientDetail {
  workspace_patient_id: string;
  display_name: string;
  first_name: string | null;
  last_name: string | null;
  sex: string | null;
  dob: string | null;
  status: string | null;
  modified?: string;
  editable?: boolean;
}

const DETAIL_KEYS: ReadonlySet<string> = new Set([
  "workspace_patient_id",
  "display_name",
  "first_name",
  "last_name",
  "sex",
  "dob",
  "status",
  "modified",
  "editable",
]);

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isOptionalText(value: unknown): value is string | null {
  return value === null || isText(value);
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Calendar validity, not just shape: `2023-02-29` and `1990-13-01` fail. */
function isCalendarDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText] = match;
  if (
    yearText === undefined ||
    monthText === undefined ||
    dayText === undefined
  )
    return false;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

/**
 * Strict wire validation. An extra key (a leaked native id, mobile number or
 * any other record field) is a failed read, never silently dropped, and the
 * response must carry the alias that was requested.
 */
function parseLivePatientDetail(
  body: unknown,
  workspacePatientId: string,
): LivePatientDetail {
  if (!isRecord(body)) throw unavailable();
  const keys = Object.keys(body);
  if (keys.some((key) => !DETAIL_KEYS.has(key))) throw unavailable();

  const {
    workspace_patient_id,
    display_name,
    first_name,
    last_name,
    sex,
    dob,
    status,
    modified,
    editable,
  } = body;

  if (!isText(workspace_patient_id) || !isText(display_name))
    throw unavailable();
  if (workspace_patient_id !== workspacePatientId) throw unavailable();
  if (!isOptionalText(first_name)) throw unavailable();
  if (!isOptionalText(last_name)) throw unavailable();
  if (!isOptionalText(sex)) throw unavailable();
  if (!isOptionalText(status)) throw unavailable();
  if (modified !== undefined && !isText(modified)) throw unavailable();
  if (editable !== undefined && typeof editable !== "boolean") throw unavailable();
  if (dob !== null && (!isText(dob) || !isCalendarDate(dob)))
    throw unavailable();

  return {
    workspace_patient_id,
    display_name,
    first_name,
    last_name,
    sex,
    dob,
    status,
    ...(modified === undefined ? {} : { modified }),
    ...(editable === undefined ? {} : { editable }),
  };
}

async function fetchLivePatient(workspacePatientId: string, signal: AbortSignal) {
  // The detail read exists only on the native transport. Without it the caller
  // must show an unavailable state: no proxy or fixture read stands in.
  if (!nativeTransportEnabled())
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  const body = await fetchNativePatientDetailBody(workspacePatientId, signal);
  return parseLivePatientDetail(body, workspacePatientId);
}

export function useLivePatient(workspacePatientId: string | undefined) {
  const { status, session, authGeneration } = useSession();
  return useQuery<LivePatientDetail, AppError>({
    queryKey: [
      "live-patient",
      session?.mode,
      authGeneration,
      workspacePatientId,
    ],
    enabled:
      status === "authenticated" &&
      session?.mode === "live" &&
      workspacePatientId !== undefined &&
      session.user.roles.some(
        (role) => role === "DOC" || role === "REC" || role === "NUR",
      ),
    queryFn: ({ signal }) =>
      fetchLivePatient(workspacePatientId as string, signal),
    retry: false,
  });
}
