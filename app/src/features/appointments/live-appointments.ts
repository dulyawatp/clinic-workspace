import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../../transport/error-map";
import {
  fetchNativeAppointmentsBody,
  readNativeAppointmentItem,
} from "../../transport/native/appointment";
import { nativeTransportEnabled } from "../../transport/native/flag";
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

async function fetchProxyAppointmentsBody(
  signal: AbortSignal,
): Promise<unknown> {
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
  return body;
}

/**
 * Accepts the canonical native fields and the historical workspace aliases.
 * Unknown extra fields are ignored, and the duplicate-reference check keeps the
 * one-row-per-appointment contract.
 */
function parseLiveAppointments(body: unknown): LiveAppointment[] {
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
    const row = readNativeAppointmentItem(item);
    if (seen.has(row.appointment_id)) throw unavailable();
    seen.add(row.appointment_id);
    return row;
  });
}

async function fetchLiveAppointments(
  signal: AbortSignal,
): Promise<LiveAppointment[]> {
  const body = nativeTransportEnabled()
    ? await fetchNativeAppointmentsBody(signal)
    : await fetchProxyAppointmentsBody(signal);
  return parseLiveAppointments(body);
}

export function useLiveAppointments() {
  const { status, session, authGeneration } = useSession();
  return useQuery<LiveAppointment[], AppError>({
    queryKey: ["live-appointments", session?.mode, authGeneration],
    enabled:
      status === "authenticated" &&
      session?.mode === "live" &&
      session.user.roles.some((role) => role === "REC" || role === "DOC"),
    queryFn: ({ signal }) => fetchLiveAppointments(signal),
    retry: false,
  });
}
