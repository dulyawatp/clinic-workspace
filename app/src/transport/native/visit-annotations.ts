import type { VisitPhotoSlot } from "./visit-photos";
import { z } from "zod";
import { MAX_VISIT_SHAPES, MAX_VISIT_STROKE_POINTS, MAX_VISIT_TEXT } from "../../features/photos/annotate/visit-drawing";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

type Point = { readonly x: number; readonly y: number };
type Mark = { readonly id: string; readonly color: string };
type Position = Mark & Point;

export interface VisitDrawing {
  readonly schema_version: 2;
  readonly coordinate_space: "normalized";
  readonly shapes: readonly (
    | Mark & { type: "freehand"; stroke_width: number; points: readonly Point[] }
    | Position & { type: "arrow"; stroke_width: number; end_x: number; end_y: number }
    | Position & { type: "rectangle" | "ellipse"; stroke_width: number; width: number; height: number }
    | Position & { type: "text"; font_size: number; text: string }
  )[];
}

type Slot = { appointment: string; slot: VisitPhotoSlot };
export type VisitAnnotationTarget = Slot & { encounter: string };
type Submission = "pending" | "uncertain" | "stale" | "rejected";

export interface VisitAnnotationRead {
  readonly version: number;
  readonly annotation: VisitDrawing | null;
  /** Verified for the exact linked draft and active grant; never infer from capture permission. */
  readonly canWrite: boolean;
  readonly pendingRequestKey: string | null;
}

// Renderer boundary; callbacks remain unwired until native capability gating exists.
export interface VisitAnnotationCallbacks {
  read(input: Slot & { version?: number }, signal: AbortSignal): Promise<VisitAnnotationRead | null>;
  submit(input: VisitAnnotationTarget & {
    expected_version: number;
    request_key: string;
    annotation: VisitDrawing;
  }, signal: AbortSignal): Promise<Submission>;
  status(input: Slot & { request_key: string }, signal: AbortSignal): Promise<
    { state: Submission } | { state: "committed"; request_key: string; version: number }
  >;
}

const reference = z.string().min(1).refine((value) =>
  [...value].length <= 140 && value.trim() === value && value !== "XXXXXXXX" && !/[\p{Cc}\p{Cf}]/u.test(value));
const requestKey = z.string().regex(/^[A-Za-z0-9_-]{16,128}$/);
const version = z.number().int().nonnegative().safe();
const slot = z.enum(["Before", "After"]);
const unit = z.number().finite().min(0).max(1);
const mark = { id: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/), color: z.string().regex(/^#[0-9a-fA-F]{6}$/) };
const point = z.strictObject({ x: unit, y: unit });
const stroke = z.number().finite().gt(0).max(0.1);
const shape = z.discriminatedUnion("type", [
  z.strictObject({ ...mark, type: z.literal("freehand"), stroke_width: stroke,
    points: z.array(point).min(2).max(MAX_VISIT_STROKE_POINTS) }),
  z.strictObject({ ...mark, type: z.literal("arrow"), x: unit, y: unit, end_x: unit, end_y: unit, stroke_width: stroke }),
  z.strictObject({ ...mark, type: z.enum(["rectangle", "ellipse"]), x: unit, y: unit,
    width: z.number().finite().positive(), height: z.number().finite().positive(), stroke_width: stroke })
    .refine((value) => value.x + value.width <= 1 && value.y + value.height <= 1),
  z.strictObject({ ...mark, type: z.literal("text"), x: unit, y: unit,
    text: z.string().refine((value) => [...value].length > 0 && [...value].length <= MAX_VISIT_TEXT &&
      !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)),
    font_size: z.number().finite().gt(0).max(0.25) }),
]);
const drawing = z.strictObject({ schema_version: z.literal(2), coordinate_space: z.literal("normalized"),
  shapes: z.array(shape).max(MAX_VISIT_SHAPES) }).refine((value) => {
  const ids = new Set(value.shapes.map((item) => item.id));
  const points = value.shapes.reduce((sum, item) => sum + (item.type === "freehand" ? item.points.length : 0), 0);
  // Python's canonical encoder escapes non-ASCII as UTF-16 \\u sequences.
  const encoded = JSON.stringify(value).replace(/[\u007f-\uffff]/g, (char) =>
    `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  return ids.size === value.shapes.length && points <= 8192 && encoded.length <= 256 * 1024;
});
const target = { appointment: reference, slot };
const readInput = z.strictObject({ ...target, version: version.optional() });
const submitInput = z.strictObject({ ...target, encounter: reference, expected_version: version,
  request_key: requestKey, annotation: drawing });
const statusInput = z.strictObject({ ...target, request_key: requestKey });
const readResult = z.strictObject({ version, annotation: drawing.nullable(), can_write: z.boolean(),
  pending_request_key: requestKey.nullable() });
const noncommitted = z.strictObject({ state: z.enum(["pending", "uncertain", "stale", "rejected"]) });
const committed = z.strictObject({ state: z.literal("committed"), request_key: requestKey, version: version.positive() });
const envelope = z.strictObject({ message: z.unknown() });

async function request(method: "read" | "submit" | "status", payload: object, signal: AbortSignal) {
  const csrf = nativeCsrfToken();
  if (!csrf) return null;
  try {
    const name = `clinic_photos.api.visit_annotations.${method}`;
    const response = await frappeRequest(name, {
      method: "POST", mode: "same-origin", redirect: "error", signal,
      headers: { accept: "application/json", "Content-Type": "application/json", [FRAPPE_CSRF_HEADER]: csrf },
      body: JSON.stringify({ payload }),
    });
    if (response.status !== 200 || response.redirected ||
        (response.url && response.url !== new URL(`/api/method/${name}`, location.origin).href)) return null;
    return envelope.safeParse(await response.json()).data?.message ?? null;
  } catch {
    return null;
  }
}

export const nativeVisitAnnotationCallbacks: VisitAnnotationCallbacks = {
  async read(input, signal) {
    if (!readInput.safeParse(input).success) return null;
    const value = readResult.safeParse(await request("read", input, signal)).data;
    return value ? { version: value.version, annotation: value.annotation,
      canWrite: value.can_write, pendingRequestKey: value.pending_request_key } : null;
  },
  async submit(input, signal) {
    if (!submitInput.safeParse(input).success) return "rejected";
    return noncommitted.safeParse(await request("submit", input, signal)).data?.state ?? "uncertain";
  },
  async status(input, signal) {
    if (!statusInput.safeParse(input).success) return { state: "uncertain" };
    const message = await request("status", input, signal);
    const done = committed.safeParse(message).data;
    if (done) return done.request_key === input.request_key ? done : { state: "uncertain" };
    return noncommitted.safeParse(message).data ?? { state: "uncertain" };
  },
};
