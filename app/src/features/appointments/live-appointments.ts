import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../../transport/error-map";
import { assertLegacyRoute } from "../../transport/native/legacy";

export interface LiveAppointment {
  workspace_patient_id: string;
  appointment_id: string;
  appointment_date: string;
  appointment_time: string;
  status: string;
}

const unavailable = (): AppError => ({
  code: "UPSTREAM_UNAVAILABLE",
  retryable: true,
});
const safeId = (value: unknown): value is string =>
  typeof value === "string" &&
  value.trim() === value &&
  value.length > 0 &&
  value.length <= 256 &&
  ![...value].some((char) => {
    const code = char.charCodeAt(0);
    return code < 32 || code === 127;
  });

function validDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const [year, month, day] = value.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined)
    return false;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

async function fetchAppointments(
  signal: AbortSignal,
): Promise<LiveAppointment[]> {
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch("/api/live/synthetic-appointments", {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch {
    throw unavailable();
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401)
    throw { code: "AUTH_EXPIRED", retryable: false } satisfies AppError;
  if (response.status === 403)
    throw { code: "FORBIDDEN", retryable: false } satisfies AppError;
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  if (
    !body ||
    typeof body !== "object" ||
    !("items" in body) ||
    !Array.isArray(body.items) ||
    body.items.length > 1000
  )
    throw unavailable();
  const seen = new Set<string>();
  return body.items.map((item: unknown) => {
    if (!item || typeof item !== "object" || Array.isArray(item))
      throw unavailable();
    const row = item as Record<string, unknown>;
    if (
      !safeId(row.workspace_patient_id) ||
      !safeId(row.appointment_id) ||
      !validDate(row.appointment_date) ||
      typeof row.appointment_time !== "string" ||
      !/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(row.appointment_time) ||
      typeof row.status !== "string" ||
      !/^[A-Za-z][A-Za-z0-9 _-]{0,63}$/.test(row.status) ||
      seen.has(row.appointment_id)
    )
      throw unavailable();
    seen.add(row.appointment_id);
    return {
      workspace_patient_id: row.workspace_patient_id,
      appointment_id: row.appointment_id,
      appointment_date: row.appointment_date,
      appointment_time: row.appointment_time,
      status: row.status,
    };
  });
}

export function useLiveAppointments() {
  const { status, session, authGeneration } = useSession();
  return useQuery<LiveAppointment[], AppError>({
    queryKey: ["live-appointments", session?.mode, authGeneration],
    enabled:
      status === "authenticated" &&
      session?.mode === "live" &&
      session.user.roles.some((role) => role === "REC" || role === "DOC"),
    queryFn: ({ signal }) => fetchAppointments(signal),
    retry: false,
  });
}
