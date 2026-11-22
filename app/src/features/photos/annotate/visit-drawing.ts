import type { VisitDrawing } from "../../../transport/native/visit-annotations";
import type { LayerShape, Point } from "./engine";

export const MAX_VISIT_STROKE_POINTS = 1024;
export const MAX_VISIT_SHAPES = 512;
export const MAX_VISIT_TEXT = 1024;
const DEFAULT_STROKE = 0.008;
const DEFAULT_FONT = 0.04;
const MAX_BYTES = 256 * 1024;

const sample = (points: readonly Point[], size: number) => size >= points.length
  ? points
  : Array.from({ length: size }, (_, i) => points[Math.round(i * (points.length - 1) / (size - 1))]!);

/** Preserve both endpoints and cap total points and bytes, with no dropped marks. */
export function encodeVisitDrawing(shapes: readonly LayerShape[]): VisitDrawing | null {
  if (shapes.length > MAX_VISIT_SHAPES) return null;
  const strokes = shapes.filter((shape) => shape.kind === "freehand");
  const reserved = strokes.length * 2;
  const extra = strokes.reduce((sum, shape) => sum + Math.min(MAX_VISIT_STROKE_POINTS, shape.points.length) - 2, 0);
  for (let budget = Math.min(extra, 8192 - reserved); ; budget = Math.floor(budget / 2)) {
    const document: VisitDrawing = {
      schema_version: 2, coordinate_space: "normalized",
      shapes: shapes.map((shape) => {
        const mark = { id: shape.id, color: shape.stroke };
        if (shape.kind === "freehand") {
          const size = 2 + Math.floor((Math.min(MAX_VISIT_STROKE_POINTS, shape.points.length) - 2) * (extra ? budget / extra : 0));
          return { ...mark, type: "freehand", stroke_width: shape.strokeWidth / 100, points: sample(shape.points, size) };
        }
        if (shape.kind === "arrow") return { ...mark, type: "arrow", ...shape.from, end_x: shape.to.x, end_y: shape.to.y,
          stroke_width: shape.strokeWidth === undefined ? DEFAULT_STROKE : shape.strokeWidth / 100 };
        const position = { ...mark, x: shape.x, y: shape.y };
        if (shape.kind === "text") return { ...position, type: "text", text: shape.text,
          font_size: shape.fontSize === undefined ? DEFAULT_FONT : shape.fontSize / 100 };
        return { ...position, type: shape.kind === "rect" ? "rectangle" : "ellipse",
          width: Math.min(1 - shape.x, shape.kind === "rect" ? shape.width : shape.radiusX * 2),
          height: Math.min(1 - shape.y, shape.kind === "rect" ? shape.height : shape.radiusY * 2),
          stroke_width: shape.strokeWidth === undefined ? DEFAULT_STROKE : shape.strokeWidth / 100 };
      }),
    };
    if (new TextEncoder().encode(JSON.stringify(document)).length <= MAX_BYTES) return document;
    if (budget <= 0) return null;
  }
}

export function decodeVisitDrawing(document: VisitDrawing): LayerShape[] {
  return document.shapes.map((shape) => {
    const mark = { id: shape.id, stroke: shape.color };
    if (shape.type === "freehand") return { ...mark, kind: "freehand", strokeWidth: shape.stroke_width * 100, points: shape.points };
    if (shape.type === "text") return { ...mark, kind: "text", x: shape.x, y: shape.y, text: shape.text,
      ...(shape.font_size !== DEFAULT_FONT && { fontSize: shape.font_size * 100 }) };
    const style = { ...mark, ...(shape.stroke_width !== DEFAULT_STROKE && { strokeWidth: shape.stroke_width * 100 }) };
    if (shape.type === "arrow") return { ...style, kind: "arrow",
      from: { x: shape.x, y: shape.y }, to: { x: shape.end_x, y: shape.end_y } };
    const position = { ...style, x: shape.x, y: shape.y };
    return shape.type === "rectangle" ? { ...position, kind: "rect", width: shape.width, height: shape.height }
      : { ...position, kind: "ellipse", radiusX: shape.width / 2, radiusY: shape.height / 2 };
  });
}
