import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import {
  fetchNativeAppointmentDetailBody,
  type NativeAppointmentRead,
  parseNativeAppointmentRead,
} from "../../transport/native/appointment";
import { nativeTransportEnabled } from "../../transport/native/flag";

export type LiveAppointmentDetail = NativeAppointmentRead;

export const liveAppointmentQueryKey = (
  mode: string | undefined,
  authGeneration: number,
  appointmentId: string | undefined,
) => ["live-appointment", mode, authGeneration, appointmentId] as const;

async function fetchLiveAppointment(
  appointmentId: string,
  signal: AbortSignal,
): Promise<LiveAppointmentDetail> {
  // The versioned detail exists only on the native transport. Without it the
  // caller must show an unavailable state; no proxy or fixture stands in.
  if (!nativeTransportEnabled())
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  const body = await fetchNativeAppointmentDetailBody(appointmentId, signal);
  return parseNativeAppointmentRead(body, appointmentId);
}

export function useLiveAppointment(appointmentId: string | undefined) {
  const { status, session, authGeneration } = useSession();
  return useQuery<LiveAppointmentDetail, AppError>({
    queryKey: liveAppointmentQueryKey(
      session?.mode,
      authGeneration,
      appointmentId,
    ),
    enabled:
      status === "authenticated" &&
      session?.mode === "live" &&
      appointmentId !== undefined &&
      session.user.roles.some((role) => role === "REC" || role === "DOC"),
    queryFn: ({ signal }) =>
      fetchLiveAppointment(appointmentId as string, signal),
    retry: false,
  });
}
