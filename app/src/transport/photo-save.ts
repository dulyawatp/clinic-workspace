import { CSRF_HEADER, fetchCsrf } from "./auth";
import { assertLegacyRoute } from "./native/legacy";

export type PhotoOutcome = {
  status: "pending" | "unknown" | "conflict" | "rejected" | "saved";
  operation_id: string;
};

async function post(path: string, body: object): Promise<PhotoOutcome> {
  // Photo writes are proxy-only; the native build has no write contract.
  assertLegacyRoute();
  const { token } = await fetchCsrf();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      [CSRF_HEADER]: token,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  const data: unknown = await response.json();
  if (
    !response.ok &&
    (typeof data !== "object" ||
      data === null ||
      !("status" in data) ||
      (data.status !== "rejected" && data.status !== "conflict"))
  )
    throw new Error("Photo request failed");
  if (
    typeof data !== "object" ||
    data === null ||
    !("status" in data) ||
    !("operation_id" in data) ||
    typeof data.operation_id !== "string" ||
    !data.operation_id.trim() ||
    !["pending", "unknown", "conflict", "rejected", "saved"].includes(
      String(data.status),
    )
  )
    throw new Error("Invalid photo outcome");
  return data as PhotoOutcome;
}

export function savePhoto(
  syntheticPatient: string,
  requestKey: string,
  imageBase64: string,
) {
  return post("/api/write/photo-save", {
    kind: "synthetic_original_create",
    request_key: requestKey,
    synthetic_patient: syntheticPatient,
    image_base64: imageBase64,
  });
}

export function photoStatus(operationId: string) {
  return post("/api/write/photo-status", { operation_id: operationId });
}
