import { createHash } from "node:crypto";
import type { FastifyInstance, FastifyReply } from "fastify";
import type { RoleCode } from "../capabilities.js";
import type { LiveUpstream } from "../config.js";
import {
  invalidateLiveAccess,
  type LiveAccessDeps,
  liveUpstream,
  requireLiveAccess,
} from "./live-access.js";
import {
  probeNativeSession,
  readAuthorizedPatient,
} from "./patient-authority.js";

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;
const MAX_PHOTO_BYTES = 8 * 1024 * 1024;
const MAX_LIST_RESPONSE_BYTES = 512 * 1024;
const MAX_META_RESPONSE_BYTES = 128 * 1024;

/**
 * Photo reads are open to both bedside and treating roles, matching the fixture
 * reader (`clinical-photos`). Native per-user permissions still decide every
 * listing and every byte read; this is only the proxy-side role gate.
 */
const PHOTO_READ_ROLES: RoleCode[] = ["DOC", "NUR"];

const REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$/;
const DOC_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,139}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const PRIVATE_URL = /^\/private\/files\/[A-Za-z0-9][A-Za-z0-9._/-]*$/;

const PHOTO_FIELDS = [
  "name",
  "patient_reference",
  "original_file",
  "original_sha256",
  "original_width",
  "original_height",
  "creation",
] as const;

const FILE_FIELDS = [
  "file_url",
  "file_name",
  "is_private",
  "file_size",
] as const;

interface PhotoMetadata {
  photo_id: string;
  patient_reference: string;
  sha256: string;
  width: number;
  height: number;
  created_at: string;
  content_url: string;
}

interface PhotoRecord extends PhotoMetadata {
  /** Internal File docname; never serialised to the client. */
  originalFile: string;
}

interface FileMetadata {
  fileUrl: string;
  contentLength: number | null;
}

type ReadResult =
  | { state: "ok"; bytes: Buffer }
  | { state: "too_large" }
  | { state: "unavailable" };

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

function isSafeReference(value: string): boolean {
  return REFERENCE.test(value) && !value.includes("..");
}

function isSafeDocName(value: unknown): value is string {
  return (
    typeof value === "string" && DOC_NAME.test(value) && !value.includes("..")
  );
}

function isPrivateUrl(value: unknown): value is string {
  if (typeof value !== "string" || !PRIVATE_URL.test(value)) return false;
  if (value.includes("..") || value.includes("\\") || value.includes("//"))
    return false;
  return !value.toLowerCase().includes("%2f");
}

async function readBoundedJson(
  response: Response,
  maxBytes: number,
): Promise<unknown> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > maxBytes) {
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  return JSON.parse(text);
}

async function readBoundedBytes(
  response: Response,
  maxBytes: number,
): Promise<ReadResult> {
  const body = response.body;
  if (body === null) {
    return { state: "unavailable" };
  }
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value === undefined) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => {});
        return { state: "too_large" };
      }
      chunks.push(value);
    }
  } catch {
    return { state: "unavailable" };
  } finally {
    reader.releaseLock();
  }
  return { state: "ok", bytes: Buffer.concat(chunks) };
}

function parseInteger(raw: unknown, fallback: number): number {
  if (typeof raw !== "string" || raw.trim() === "") return fallback;
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 0) return fallback;
  return value;
}

function nativeUrl(
  upstream: LiveUpstream,
  path: string,
  params: Record<string, string>,
): string {
  const query = new URLSearchParams(params).toString();
  return `${upstream.origin}${path}?${query}`;
}

function photoMetadataFromRow(
  reference: string,
  row: unknown,
  nativePatient: string,
): PhotoRecord | null {
  if (typeof row !== "object" || row === null) return null;
  const record = row as Record<string, unknown>;
  if (
    !isSafeDocName(record.name) ||
    record.patient_reference !== nativePatient ||
    !isSafeDocName(record.original_file) ||
    typeof record.original_sha256 !== "string" ||
    !SHA256.test(record.original_sha256) ||
    typeof record.original_width !== "number" ||
    !Number.isSafeInteger(record.original_width) ||
    record.original_width <= 0 ||
    typeof record.original_height !== "number" ||
    !Number.isSafeInteger(record.original_height) ||
    record.original_height <= 0 ||
    typeof record.creation !== "string" ||
    record.creation.trim() === ""
  ) {
    return null;
  }

  return {
    photo_id: record.name,
    patient_reference: nativePatient,
    originalFile: record.original_file,
    sha256: record.original_sha256,
    width: record.original_width,
    height: record.original_height,
    created_at: record.creation,
    content_url: `/api/live/synthetic-patients/${encodeURIComponent(
      reference,
    )}/photos/${encodeURIComponent(record.name)}/content`,
  };
}

function fileMetadataFromRow(row: unknown): FileMetadata | null {
  if (typeof row !== "object" || row === null) return null;
  const record = row as Record<string, unknown>;
  if (
    !isPrivateUrl(record.file_url) ||
    !isSafeDocName(record.file_name) ||
    (record.is_private !== 1 && record.is_private !== true)
  ) {
    return null;
  }
  const rawSize = record.file_size;
  if (
    typeof rawSize !== "number" ||
    !Number.isSafeInteger(rawSize) ||
    rawSize <= 0
  ) {
    return null;
  }
  return { fileUrl: record.file_url, contentLength: rawSize };
}

function bytesArePng(bytes: Buffer): boolean {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  return (
    bytes.length >= signature.length &&
    signature.every((value, index) => bytes[index] === value)
  );
}

function bytesAreJpeg(bytes: Buffer): boolean {
  return (
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff
  );
}

export function registerLivePhotoRoutes(
  app: FastifyInstance,
  deps: LiveAccessDeps & { fetchImpl: typeof fetch },
): void {
  const { config, fetchImpl } = deps;
  const upstream = liveUpstream(config);

  async function deny(
    reply: FastifyReply,
    access: { sessionId: string; native: { sid: string; csrf: string } },
    live: LiveUpstream,
  ) {
    const session = await probeNativeSession(live, fetchImpl, access.native);
    if (session === "expired") {
      invalidateLiveAccess(reply, deps, access.sessionId);
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }
    return sendCode(reply, 403, "FORBIDDEN");
  }

  app.get(
    "/api/live/synthetic-patients/:reference/photos",
    async (req, reply) => {
      reply.header("cache-control", "no-store");

      const access = requireLiveAccess(
        req,
        reply,
        deps,
        upstream,
        PHOTO_READ_ROLES,
      );
      if (access === null || upstream === undefined) return reply;

      const params = req.params as Record<string, unknown>;
      const reference = params.reference;
      if (typeof reference !== "string" || !isSafeReference(reference)) {
        return sendCode(reply, 400, "VALIDATION");
      }
      const nativePatient = upstream.syntheticPatients.get(reference);
      if (nativePatient === undefined) {
        return sendCode(reply, 400, "VALIDATION");
      }

      const query = req.query as Record<string, unknown>;
      const limit = Math.max(
        1,
        Math.min(parseInteger(query.limit, DEFAULT_LIMIT), MAX_LIMIT),
      );
      const offset = parseInteger(query.offset, 0);

      const authority = await readAuthorizedPatient(
        upstream,
        fetchImpl,
        access.native,
        nativePatient,
      );
      if (authority.state === "expired") {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (authority.state === "denied") {
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (authority.state !== "authorized") {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      let response: Response;
      try {
        response = await fetchImpl(
          nativeUrl(upstream, "/api/resource/Clinic%20Photo", {
            filters: JSON.stringify([
              ["patient_reference", "=", nativePatient],
            ]),
            fields: JSON.stringify(PHOTO_FIELDS),
            limit: String(limit),
            limit_start: String(offset),
            order_by: "creation desc",
          }),
          {
            method: "GET",
            headers: {
              accept: "application/json",
              cookie: `sid=${access.native.sid}`,
            },
            redirect: "manual",
            signal: AbortSignal.timeout(upstream.timeoutMs),
          },
        );
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      if (response.status === 401) {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (response.status === 403 || response.status === 404) {
        return deny(reply, access, upstream);
      }
      if (response.status !== 200) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      let body: unknown;
      try {
        body = await readBoundedJson(response, MAX_LIST_RESPONSE_BYTES);
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
      const rows =
        typeof body === "object" && body !== null
          ? (body as Record<string, unknown>).data
          : undefined;
      if (!Array.isArray(rows) || rows.length > limit) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const items: PhotoMetadata[] = [];
      for (const row of rows) {
        const photo = photoMetadataFromRow(reference, row, nativePatient);
        // A cross-patient or malformed row is never described to the client.
        if (photo === null) {
          return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
        }
        items.push({
          photo_id: photo.photo_id,
          patient_reference: photo.patient_reference,
          sha256: photo.sha256,
          width: photo.width,
          height: photo.height,
          created_at: photo.created_at,
          content_url: photo.content_url,
        });
      }

      return reply.code(200).send({ items });
    },
  );

  app.get(
    "/api/live/synthetic-patients/:reference/photos/:photoId/content",
    async (req, reply) => {
      reply.header("cache-control", "no-store");

      const access = requireLiveAccess(
        req,
        reply,
        deps,
        upstream,
        PHOTO_READ_ROLES,
      );
      if (access === null || upstream === undefined) return reply;

      const params = req.params as Record<string, unknown>;
      const reference = params.reference;
      const photoId = params.photoId;
      if (
        typeof reference !== "string" ||
        !isSafeReference(reference) ||
        typeof photoId !== "string" ||
        !isSafeDocName(photoId)
      ) {
        return sendCode(reply, 400, "VALIDATION");
      }
      const nativePatient = upstream.syntheticPatients.get(reference);
      if (nativePatient === undefined) {
        return sendCode(reply, 400, "VALIDATION");
      }

      const authority = await readAuthorizedPatient(
        upstream,
        fetchImpl,
        access.native,
        nativePatient,
      );
      if (authority.state === "expired") {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (authority.state === "denied") {
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (authority.state !== "authorized") {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      // Bind the photo row to the mapped patient before reading any file.
      let photoResponse: Response;
      try {
        photoResponse = await fetchImpl(
          nativeUrl(
            upstream,
            `/api/resource/Clinic%20Photo/${encodeURIComponent(photoId)}`,
            { fields: JSON.stringify(PHOTO_FIELDS) },
          ),
          {
            method: "GET",
            headers: {
              accept: "application/json",
              cookie: `sid=${access.native.sid}`,
            },
            redirect: "manual",
            signal: AbortSignal.timeout(upstream.timeoutMs),
          },
        );
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      if (photoResponse.status === 401) {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (photoResponse.status === 403 || photoResponse.status === 404) {
        return deny(reply, access, upstream);
      }
      if (photoResponse.status !== 200) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      let photoBody: unknown;
      try {
        photoBody = await readBoundedJson(
          photoResponse,
          MAX_META_RESPONSE_BYTES,
        );
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
      const photoRow =
        typeof photoBody === "object" && photoBody !== null
          ? (photoBody as Record<string, unknown>).data
          : undefined;
      const photo = photoMetadataFromRow(reference, photoRow, nativePatient);
      if (photo === null) {
        // Includes the cross-patient case: never expose another patient's row.
        return sendCode(reply, 403, "FORBIDDEN");
      }

      let fileResponse: Response;
      try {
        fileResponse = await fetchImpl(
          nativeUrl(
            upstream,
            `/api/resource/File/${encodeURIComponent(photo.originalFile)}`,
            { fields: JSON.stringify(FILE_FIELDS) },
          ),
          {
            method: "GET",
            headers: {
              accept: "application/json",
              cookie: `sid=${access.native.sid}`,
            },
            redirect: "manual",
            signal: AbortSignal.timeout(upstream.timeoutMs),
          },
        );
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      if (fileResponse.status === 401) {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (fileResponse.status === 403 || fileResponse.status === 404) {
        return deny(reply, access, upstream);
      }
      if (fileResponse.status !== 200) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      let fileBody: unknown;
      try {
        fileBody = await readBoundedJson(fileResponse, MAX_META_RESPONSE_BYTES);
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
      const file = fileMetadataFromRow(
        typeof fileBody === "object" && fileBody !== null
          ? (fileBody as Record<string, unknown>).data
          : undefined,
      );
      if (file === null) {
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (file.contentLength !== null && file.contentLength > MAX_PHOTO_BYTES) {
        return sendCode(reply, 413, "FILE_TOO_LARGE");
      }

      // Native protected delivery: Frappe's `/private/files/` route re-checks
      // `find_file_by_url` + dynamic `is_downloadable` against this clinician's
      // session, then streams the stored bytes. `frappe.handler.download_file`
      // is deliberately not used: it goes through `File.get_content`, which can
      // return a decoded `str` for binary that happens to decode, corrupting the
      // body (observed as a 415 on a valid short PNG).
      //
      // `file.fileUrl` is the server-derived canonical `/private/files/...`
      // path from the checked `File` row above (never a browser value), so the
      // URL below is always same-origin and canonical.
      const contentUrl = `${upstream.origin}${file.fileUrl}`;
      let download: Response;
      try {
        download = await fetchImpl(contentUrl, {
          method: "GET",
          headers: {
            accept: "image/png,image/jpeg",
            cookie: `sid=${access.native.sid}`,
          },
          redirect: "manual",
          signal: AbortSignal.timeout(upstream.timeoutMs),
        });
      } catch {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      if (download.status === 401) {
        invalidateLiveAccess(reply, deps, access.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (download.status === 403 || download.status === 404) {
        return deny(reply, access, upstream);
      }
      if (download.status !== 200) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const read = await readBoundedBytes(download, MAX_PHOTO_BYTES);
      if (read.state === "too_large") {
        return sendCode(reply, 413, "FILE_TOO_LARGE");
      }
      if (read.state !== "ok") {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const png = bytesArePng(read.bytes);
      const jpeg = bytesAreJpeg(read.bytes);
      if (!png && !jpeg) {
        return sendCode(reply, 415, "UNSUPPORTED");
      }
      const digest = createHash("sha256").update(read.bytes).digest("hex");
      if (digest !== photo.sha256) {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      reply.header("content-type", png ? "image/png" : "image/jpeg");
      reply.header("content-length", String(read.bytes.length));
      reply.header("x-content-type-options", "nosniff");
      return reply.code(200).send(read.bytes);
    },
  );
}
