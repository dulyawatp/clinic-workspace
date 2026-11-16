import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../../transport/error-map";
import { fetchNativeDirectoryBody } from "../../transport/native/directory";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { assertLegacyRoute } from "../../transport/native/legacy";

export interface LivePatient {
  workspace_patient_id: string;
  display_name: string;
}

const unavailable = (): AppError => ({
  code: "UPSTREAM_UNAVAILABLE",
  retryable: true,
});

async function fetchProxyDirectoryBody(signal: AbortSignal): Promise<unknown> {
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch("/api/live/synthetic-patients", {
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

/** Native rows expose only alias and display name; proxy keeps its legacy shape. */
function parseLivePatients(body: unknown, native: boolean): LivePatient[] {
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
    if (
      !item ||
      typeof item !== "object" ||
      !("workspace_patient_id" in item) ||
      !("display_name" in item) ||
      typeof item.workspace_patient_id !== "string" ||
      !item.workspace_patient_id.trim() ||
      (native
        ? Object.keys(item).length !== 2
        : !("native_patient_id" in item) || typeof item.native_patient_id !== "string" || !item.native_patient_id.trim()) ||
      typeof item.display_name !== "string" ||
      !item.display_name.trim() ||
      [
        item.workspace_patient_id,
        ...(!native && "native_patient_id" in item && typeof item.native_patient_id === "string" ? [item.native_patient_id] : []),
        item.display_name,
      ].some(
        (value) =>
          value.length > 256 ||
          [...value].some((char) => {
            const code = char.charCodeAt(0);
            return code < 32 || code === 127;
          }),
      ) ||
      seen.has(item.workspace_patient_id)
    )
      throw unavailable();
    seen.add(item.workspace_patient_id);
    return {
      workspace_patient_id: item.workspace_patient_id,
      display_name: item.display_name,
    };
  });
}

async function fetchLivePatients(signal: AbortSignal): Promise<LivePatient[]> {
  const native = nativeTransportEnabled();
  const body = native
    ? await fetchNativeDirectoryBody(signal)
    : await fetchProxyDirectoryBody(signal);
  return parseLivePatients(body, native);
}

export function useLivePatients() {
  const { status, session, authGeneration } = useSession();
  return useQuery<LivePatient[], AppError>({
    queryKey: ["live-directory", session?.mode, authGeneration],
    enabled:
      status === "authenticated" &&
      session?.mode === "live" &&
      session.user.roles.some(
        (role) => role === "DOC" || role === "REC" || role === "NUR",
      ),
    queryFn: ({ signal }) => fetchLivePatients(signal),
    retry: false,
  });
}
