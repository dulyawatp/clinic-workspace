/**
 * PAT-05 annotation engine.
 *
 * Pure, client-side and deterministic: every operation returns a new state and
 * every history entry is a deep copy of the shapes, so undo/redo can never
 * mutate an earlier version. Nothing here reads the clock, the network or the
 * session; coordinates are normalized 0..1 over the original, which is why the
 * original file is never written to.
 */

export interface Point {
  readonly x: number;
  readonly y: number;
}

export type AnnotationTool =
  | "select"
  | "arrow"
  | "ellipse"
  | "rect"
  | "freehand"
  | "text";

export type ShapeKind = Exclude<AnnotationTool, "select">;

export type LayerShape =
  | {
      readonly id: string;
      readonly kind: "rect";
      readonly x: number;
      readonly y: number;
      readonly width: number;
      readonly height: number;
      readonly stroke: string;
    }
  | {
      readonly id: string;
      readonly kind: "ellipse";
      readonly x: number;
      readonly y: number;
      readonly radiusX: number;
      readonly radiusY: number;
      readonly stroke: string;
    }
  | {
      readonly id: string;
      readonly kind: "arrow";
      readonly from: Point;
      readonly to: Point;
      readonly stroke: string;
    }
  | {
      readonly id: string;
      readonly kind: "freehand";
      readonly points: readonly Point[];
      readonly strokeWidth: number;
      readonly stroke: string;
    }
  | {
      readonly id: string;
      readonly kind: "text";
      readonly x: number;
      readonly y: number;
      readonly text: string;
      readonly stroke: string;
    };

export interface AnnotationState {
  readonly tools: readonly AnnotationTool[];
  readonly tool: AnnotationTool;
  readonly shapes: readonly LayerShape[];
  readonly selectedId: string | null;
  /** Snapshots before each completed edit; `null` entries never occur. */
  readonly past: readonly (readonly LayerShape[])[];
  readonly future: readonly (readonly LayerShape[])[];
}

/** Bounded history: a long drawing session cannot grow without limit. */
export const HISTORY_LIMIT = 50;

export const ANNOTATION_TOOLS: readonly AnnotationTool[] = [
  "select",
  "arrow",
  "rect",
  "ellipse",
  "freehand",
  "text",
];

export function createState(shapes: readonly LayerShape[] = []): AnnotationState {
  return {
    tools: ANNOTATION_TOOLS,
    tool: "select",
    shapes: copyShapes(shapes),
    selectedId: null,
    past: [],
    future: [],
  };
}

function copyShapes(
  shapes: readonly LayerShape[],
): readonly LayerShape[] {
  return shapes.map((shape) =>
    shape.kind === "freehand"
      ? { ...shape, points: shape.points.map((point) => ({ ...point })) }
      : shape.kind === "arrow"
        ? { ...shape, from: { ...shape.from }, to: { ...shape.to } }
        : { ...shape },
  );
}

function pushPast(
  past: readonly (readonly LayerShape[])[],
  snapshot: readonly LayerShape[],
): readonly (readonly LayerShape[])[] {
  const next = [...past, snapshot];
  return next.length > HISTORY_LIMIT ? next.slice(-HISTORY_LIMIT) : next;
}

/** Records the pre-edit shapes so a gesture can be undone as one step. */
export function checkpoint(state: AnnotationState): AnnotationState {
  return {
    ...state,
    past: pushPast(state.past, copyShapes(state.shapes)),
    future: [],
  };
}

/** Replaces the working shapes without a new history entry. */
export function replaceShapes(
  state: AnnotationState,
  shapes: readonly LayerShape[],
): AnnotationState {
  return { ...state, shapes: copyShapes(shapes) };
}

export function chooseTool(
  state: AnnotationState,
  tool: AnnotationTool,
): AnnotationState {
  return { ...state, tool };
}

export function selectShape(
  state: AnnotationState,
  id: string | null,
): AnnotationState {
  return { ...state, selectedId: id };
}

export function addShape(
  state: AnnotationState,
  shape: LayerShape,
): AnnotationState {
  const next = checkpoint(state);
  return {
    ...next,
    shapes: [...next.shapes, shape],
    selectedId: shape.id,
  };
}

export function deleteSelected(state: AnnotationState): AnnotationState {
  if (state.selectedId === null) return state;
  const next = checkpoint(state);
  return {
    ...next,
    shapes: next.shapes.filter((shape) => shape.id !== state.selectedId),
    selectedId: null,
  };
}

export function undo(state: AnnotationState): AnnotationState {
  const previous = state.past.at(-1);
  if (previous === undefined) return state;
  return {
    ...state,
    shapes: copyShapes(previous),
    past: state.past.slice(0, -1),
    future: [copyShapes(state.shapes), ...state.future],
    selectedId: null,
  };
}

export function redo(state: AnnotationState): AnnotationState {
  const [next, ...rest] = state.future;
  if (next === undefined) return state;
  return {
    ...state,
    shapes: copyShapes(next),
    past: pushPast(state.past, copyShapes(state.shapes)),
    future: rest,
    selectedId: null,
  };
}

export function canUndo(state: AnnotationState): boolean {
  return state.past.length > 0;
}

export function canRedo(state: AnnotationState): boolean {
  return state.future.length > 0;
}

/** Fixture layers carry no ids; a stable seed keeps them reproducible. */
export function fromAuthoredShapes(
  shapes: readonly AuthoredShape[],
  prefix: string,
): LayerShape[] {
  return shapes.map((shape, index) => ({
    ...shape,
    id: `${prefix}-${index + 1}`,
  })) as LayerShape[];
}

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown
  ? Omit<T, K>
  : never;

/** A fixture layer before this engine assigns it a stable id. */
export type AuthoredShape = DistributiveOmit<LayerShape, "id">;

export type VersionState = "pending" | "committed";

export interface SavedAnnotationVersion {
  readonly id: string;
  readonly versionNumber: number;
  readonly state: VersionState;
  readonly shapes: readonly LayerShape[];
  readonly createdAt: string;
  readonly author: string;
  readonly sample: true;
}

/**
 * A saved layer is a new version, not an edit of the original or of an older
 * version: the previous rows stay untouched and the new one starts `pending`.
 */
export function saveVersion(
  versions: readonly SavedAnnotationVersion[],
  shapes: readonly LayerShape[],
  options: { readonly author: string; readonly at: string },
): SavedAnnotationVersion[] {
  const nextNumber =
    versions.reduce((max, version) => Math.max(max, version.versionNumber), 0) +
    1;
  return [
    ...versions,
    {
      id: `LOCAL-ANV-${nextNumber}`,
      versionNumber: nextNumber,
      state: "pending",
      shapes: copyShapes(shapes),
      createdAt: options.at,
      author: options.author,
      sample: true,
    },
  ];
}
