import { assertLegacyRoute } from "../../transport/native/legacy";

export const LIVE_PHOTO_PAGE_SIZE = 20;

export type LivePhoto = {
  photo_id: string;
  width: number;
  height: number;
  created_at: string;
  content_url: string;
};

export async function readLivePhotos(
  reference: string,
  nativePatient: string,
  offset: number,
  signal: AbortSignal,
): Promise<LivePhoto[]> {
  assertLegacyRoute();
  const base = `/api/live/synthetic-patients/${encodeURIComponent(reference)}/photos`;
  const response = await fetch(
    `${base}?limit=${LIVE_PHOTO_PAGE_SIZE}&offset=${offset}`,
    {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal,
    },
  );
  if (!response.ok) throw new Error("Live photos unavailable");
  const body: unknown = await response.json();
  if (
    !body ||
    typeof body !== "object" ||
    !("items" in body) ||
    !Array.isArray(body.items) ||
    body.items.length > LIVE_PHOTO_PAGE_SIZE
  )
    throw new Error("Invalid photo list");
  const ids = new Set<string>();
  return body.items.map((item: unknown) => {
    if (!item || typeof item !== "object") throw new Error("Invalid photo");
    const row = item as Record<string, unknown>;
    if (
      typeof row.photo_id !== "string" ||
      !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,139}$/.test(row.photo_id) ||
      row.photo_id.includes("..") ||
      ids.has(row.photo_id) ||
      row.patient_reference !== nativePatient ||
      typeof row.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(row.sha256) ||
      typeof row.width !== "number" ||
      !Number.isSafeInteger(row.width) ||
      row.width <= 0 ||
      typeof row.height !== "number" ||
      !Number.isSafeInteger(row.height) ||
      row.height <= 0 ||
      typeof row.created_at !== "string" ||
      !row.created_at.trim() ||
      typeof row.content_url !== "string"
    )
      throw new Error("Invalid photo metadata");
    const expected = `${base}/${encodeURIComponent(row.photo_id)}/content`;
    const url = new URL(row.content_url, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      url.pathname !== expected ||
      url.search ||
      url.hash ||
      url.username ||
      url.password
    )
      throw new Error("Invalid photo URL");
    ids.add(row.photo_id);
    return {
      photo_id: row.photo_id,
      width: row.width,
      height: row.height,
      created_at: row.created_at,
      content_url: expected,
    };
  });
}
