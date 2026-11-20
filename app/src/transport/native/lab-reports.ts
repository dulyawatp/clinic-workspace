import { z } from "zod";
import { mapHttpError } from "../error-map";
import type { EncounterReadError } from "./encounter-read";
import { frappeRequest, FRAPPE_CSRF_HEADER } from "./frappe";
import { classifyNativeForbidden, nativeCsrfToken } from "./session";

const method = "clinic_ui.api.lab_reports.";
const control = /[\u0000-\u001f\u007f]/;
const privateFile = "/private/files/";
const malformed = { code: "MALFORMED", retryable: true } as const satisfies EncounterReadError;
const validation = { code: "VALIDATION", retryable: false } as const satisfies EncounterReadError;
const id = z.string().max(256).refine((value) => value.trim() !== "" && !control.test(value));
const filename = z.string().min(1).max(255).refine((value) =>
  value !== "." && value !== ".." && !/[/\\]/.test(value) && !control.test(value));

function validDownload(raw: string, fileId: string): boolean {
  // Require the canonical path and exactly one fid. In particular, never
  // normalize an absolute/remote URL or a dot segment into an allowed URL.
  if (!raw.startsWith(privateFile) || control.test(raw) || raw.includes("#")) return false;
  const match = /^([^?]+)\?fid=([^&]+)$/.exec(raw.slice(privateFile.length));
  if (!match?.[1] || !match[2]) return false;
  try {
    const name = decodeURIComponent(match[1]);
    return filename.safeParse(name).success && !/%[0-9a-f]{2}/i.test(name) &&
      decodeURIComponent(match[2].replaceAll("+", " ")) === fileId;
  } catch {
    return false;
  }
}

const report = z.object({
  id,
  name: filename,
  sizeBytes: z.number().int().nonnegative(),
  uploadedBy: id,
  uploadedAt: z.string().max(64).regex(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$/),
  downloadUrl: z.string().max(2048),
}).refine((item) => validDownload(item.downloadUrl, item.id));
const page = z.object({
  items: z.array(report).max(20),
  nextOffset: z.number().int().min(1).max(1_000_000).nullable(),
});

export type LabReport = z.infer<typeof report>;
export type LabReportPage = z.infer<typeof page>;
export type LabReportContext = { kind: "appointment"; id: string } | { kind: "encounter"; id: string };

async function message(response: Response, signal?: AbortSignal): Promise<unknown> {
  if (response.status === 403 && signal) throw await classifyNativeForbidden(signal);
  if (response.status === 417) throw validation;
  if (!response.ok) throw mapHttpError(response.status, null);
  const envelope = z.object({ message: z.unknown() }).safeParse(await response.json().catch(() => null));
  if (!envelope.success) throw malformed;
  return envelope.data.message;
}

export async function listLabReports(context: LabReportContext, offset: number, signal: AbortSignal): Promise<LabReportPage> {
  if (!id.safeParse(context.id).success || !Number.isInteger(offset) || offset < 0 || offset > 1_000_000) throw validation;
  const reference = context.kind === "appointment" ? "appointment_ref" : "encounter_id";
  const params = new URLSearchParams({ [reference]: context.id, offset: String(offset) });
  const response = await frappeRequest(`${method}list_for_${context.kind}?${params}`, { signal, redirect: "error" });
  const parsed = page.safeParse(await message(response, signal));
  if (!parsed.success) throw malformed;
  const { items, nextOffset } = parsed.data;
  if ((nextOffset !== null && (items.length !== 20 || nextOffset !== offset + 20)) ||
      new Set(items.map((item) => item.id)).size !== items.length) throw malformed;
  return parsed.data;
}

export function isLabReportFile(file: File): boolean {
  return file.size > 0 && filename.safeParse(file.name).success && /\.(?:pdf|jpe?g|png)$/i.test(file.name);
}

export async function uploadLabReport(appointmentRef: string, file: File): Promise<LabReport> {
  if (!id.safeParse(appointmentRef).success || !isLabReportFile(file)) throw validation;
  const token = nativeCsrfToken();
  if (!token) throw { code: "FORBIDDEN", retryable: false } satisfies EncounterReadError;
  const body = new FormData();
  body.set("appointment_ref", appointmentRef);
  body.set("file", file, file.name);
  const response = await frappeRequest(`${method}upload`, {
    method: "POST",
    redirect: "error",
    headers: { [FRAPPE_CSRF_HEADER]: token },
    body,
  });
  const parsed = report.safeParse(await message(response));
  if (!parsed.success) throw malformed;
  return parsed.data;
}
