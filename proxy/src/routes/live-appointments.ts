import type { FastifyInstance } from "fastify";
import type { LiveUpstream } from "../config.js";
import type { NativeCredentials } from "../session/native-store.js";
import {
  invalidateLiveAccess,
  type LiveAccessDeps,
  liveUpstream,
  requireLiveAccess,
} from "./live-access.js";
import { probeNativeSession } from "./patient-authority.js";

const APPOINTMENT_PATH_PREFIX = "/api/resource/Patient%20Appointment/";
const MAX_RESPONSE_BYTES = 128 * 1024;
/** Only the two roles that own the appointment list in the new UI. */
const APPOINTMENT_ROLES = ["REC", "DOC"] as const;
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/;
const STATUS_PATTERN = /^[A-Za-z][A-Za-z0-9 _-]{0,63}$/;

/** The only appointment fields that leave the proxy. */
interface SyntheticAppointment {
  workspace_patient_id: string;
  appointment_id: string;
  appointment_date: string;
  appointment_time: string;
  status: string;
}

interface AppointmentRow {
  name: string;
  patient: string;
  date: string;
  time: string;
  status: string;
}

type AppointmentRead =
  | { state: "ok"; row: AppointmentRow }
  | { state: "denied" }
  | { state: "expired" }
  | { state: "unavailable" };

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  return JSON.parse(text);
}

function isCalendarDate(value: string): boolean {
  const match = DATE_PATTERN.exec(value);
  if (match === null) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) {
    return false;
  }
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function readRow(appointmentId: string, data: unknown): AppointmentRow | null {
  if (typeof data !== "object" || data === null) {
    return null;
  }
  const row = data as Record<string, unknown>;
  if (row.name !== appointmentId || typeof row.patient !== "string") {
    return null;
  }
  const patient = row.patient.trim();
  const date =
    typeof row.appointment_date === "string" ? row.appointment_date.trim() : "";
  const time =
    typeof row.appointment_time === "string" ? row.appointment_time.trim() : "";
  const status = typeof row.status === "string" ? row.status.trim() : "";
  if (
    patient === "" ||
    patient.length > 140 ||
    /[\r\n]/.test(patient) ||
    !isCalendarDate(date) ||
    !TIME_PATTERN.test(time) ||
    !STATUS_PATTERN.test(status)
  ) {
    return null;
  }
  return { name: appointmentId, patient, date, time, status };
}

/**
 * Reads exactly one allow-listed native `Patient Appointment` with the caller's
 * own native session. The docname always comes from the server-owned list; the
 * client never supplies it. Nothing is fabricated when the read fails.
 */
async function readSyntheticAppointment(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  appointmentId: string,
): Promise<AppointmentRead> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${APPOINTMENT_PATH_PREFIX}${encodeURIComponent(appointmentId)}`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          cookie: `sid=${native.sid}`,
        },
        redirect: "manual",
        signal: AbortSignal.timeout(upstream.timeoutMs),
      },
    );
  } catch {
    return { state: "unavailable" };
  }

  if (response.status === 401) return { state: "expired" };
  if (response.status === 403 || response.status === 404) {
    // A refusal can also mean the native session is gone. Probe read-only and
    // keep the denial when in doubt.
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired" ? { state: "expired" } : { state: "denied" };
  }
  if (response.status !== 200) return { state: "unavailable" };

  try {
    const body = await readJson(response);
    const data =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).data
        : undefined;
    const row = readRow(appointmentId, data);
    return row === null ? { state: "unavailable" } : { state: "ok", row };
  } catch {
    return { state: "unavailable" };
  }
}

/** Reverse lookup; only configured native patients have a workspace reference. */
function workspaceReferenceFor(
  upstream: LiveUpstream,
  nativePatientId: string,
): string | undefined {
  for (const [reference, native] of upstream.syntheticPatients) {
    if (native === nativePatientId) {
      return reference;
    }
  }
  return undefined;
}

export function registerLiveAppointmentRoutes(
  app: FastifyInstance,
  deps: LiveAccessDeps & { fetchImpl: typeof fetch },
): void {
  const upstream = liveUpstream(deps.config);

  app.get("/api/live/synthetic-appointments", async (req, reply) => {
    reply.header("cache-control", "no-store");

    const access = requireLiveAccess(req, reply, deps, upstream, [
      ...APPOINTMENT_ROLES,
    ]);
    if (!access || upstream === undefined) return;

    const items: SyntheticAppointment[] = [];
    for (const appointmentId of upstream.syntheticAppointments ?? []) {
      const read = await readSyntheticAppointment(
        upstream,
        deps.fetchImpl,
        access.native,
        appointmentId,
      );

      if (read.state === "expired") {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return reply.code(401).send({ code: "AUTH_EXPIRED" });
      }
      // Any failed read fails the whole response: no partial list, no fallback.
      if (read.state === "denied") {
        return reply.code(403).send({ code: "FORBIDDEN" });
      }
      if (read.state === "unavailable") {
        return reply.code(502).send({ code: "UPSTREAM_UNAVAILABLE" });
      }

      const workspacePatientId = workspaceReferenceFor(
        upstream,
        read.row.patient,
      );
      // A synthetic appointment whose patient is not on the synthetic patient
      // allow-list is omitted, never described and never looked up further.
      if (workspacePatientId === undefined) {
        continue;
      }

      items.push({
        workspace_patient_id: workspacePatientId,
        appointment_id: read.row.name,
        appointment_date: read.row.date,
        appointment_time: read.row.time,
        status: read.row.status,
      });
    }

    return reply.code(200).send({ items });
  });
}
