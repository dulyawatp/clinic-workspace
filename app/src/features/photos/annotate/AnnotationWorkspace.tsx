import { type CSSProperties, type ReactNode, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../../app/SessionProvider";
import { Badge, Button, Card, SampleBanner } from "../../../design/components";
import type { AnnotationVersion } from "../../../fixtures/data-clinical";
import type { Photo } from "../model";
import { formatDate, formatDateTime } from "../../../i18n/format";
import { useLocale } from "../../../i18n/LocaleProvider";
import {
  addShape,
  ANNOTATION_TOOLS,
  type AnnotationState,
  type AnnotationTool,
  canRedo,
  canUndo,
  checkpoint,
  chooseTool,
  createState,
  deleteSelected,
  type AuthoredShape,
  fromAuthoredShapes,
  type LayerShape,
  type Point,
  redo,
  replaceShapes,
  saveVersion,
  type SavedAnnotationVersion,
  selectShape,
  undo,
} from "./engine";
import { KonvaAnnotationSurface } from "./KonvaAnnotationSurface";
import { draftsFor, sampleFramesFor, versionsFor } from "./sample-frames";
import { MAX_VISIT_SHAPES, MAX_VISIT_STROKE_POINTS, MAX_VISIT_TEXT } from "./visit-drawing";
import "./annotate.css";

function seedShapes(versions: readonly AnnotationVersion[]): LayerShape[] {
  const latest = versions.at(-1);
  if (latest === undefined) return [];
  return fromAuthoredShapes(
    latest.shapes as readonly AuthoredShape[],
    latest.id,
  );
}

function seedVersions(
  versions: readonly AnnotationVersion[],
): SavedAnnotationVersion[] {
  return versions.map((version) => ({
    id: version.id,
    versionNumber: version.versionNumber,
    state: version.state,
    shapes: fromAuthoredShapes(
      version.shapes as readonly AuthoredShape[],
      version.id,
    ),
    createdAt: version.createdAt,
    author: version.author,
    sample: true,
  }));
}

function clamp(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function shapeOffset(shape: LayerShape, dx: number, dy: number, boundBox: boolean): LayerShape {
  switch (shape.kind) {
    case "rect":
    case "ellipse":
      if (boundBox) {
        const width = shape.kind === "rect" ? shape.width : shape.radiusX * 2;
        const height = shape.kind === "rect" ? shape.height : shape.radiusY * 2;
        return { ...shape, x: Math.min(1 - width, clamp(shape.x + dx)), y: Math.min(1 - height, clamp(shape.y + dy)) };
      }
      return { ...shape, x: clamp(shape.x + dx), y: clamp(shape.y + dy) };
    case "text":
      return { ...shape, x: clamp(shape.x + dx), y: clamp(shape.y + dy) };
    case "arrow":
      return {
        ...shape,
        from: { x: clamp(shape.from.x + dx), y: clamp(shape.from.y + dy) },
        to: { x: clamp(shape.to.x + dx), y: clamp(shape.to.y + dy) },
      };
    case "freehand":
      return {
        ...shape,
        points: shape.points.map((point) => ({
          x: clamp(point.x + dx),
          y: clamp(point.y + dy),
        })),
      };
  }
}

type Gesture = {
  readonly start: Point;
  draft: LayerShape;
} & (
  | { readonly kind: "draw" }
  | { readonly kind: "move"; readonly original: LayerShape }
  | { readonly kind: "resize"; readonly original: BoxShape }
);

type BoxShape = Extract<LayerShape, { kind: "rect" | "ellipse" }>;

function withBox(shape: BoxShape, a: Point, b: Point): BoxShape {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  const width = Math.abs(a.x - b.x);
  const height = Math.abs(a.y - b.y);
  return shape.kind === "rect"
    ? { ...shape, x, y, width, height }
    : { ...shape, x, y, radiusX: width / 2, radiusY: height / 2 };
}

function dragEdge(edge: number, anchor: number, delta: number, pixel: number) {
  // A moved box may already extend beyond the stage. Let it shrink back in
  // without snapping an untouched edge or growing beyond its current reach.
  return Math.min(Math.max(1, edge), Math.max(Math.min(edge, anchor + pixel), edge + delta));
}

function updateDraft(active: Gesture, point: Point, pixel: Point, bounded: boolean): LayerShape {
  const { start, draft } = active;
  const dx = point.x - start.x;
  const dy = point.y - start.y;
  if (active.kind === "move") return shapeOffset(active.original, dx, dy, bounded);
  if (active.kind === "resize") {
    const shape = active.original;
    if (dx === 0 && dy === 0) return shape;
    const right = shape.x + (shape.kind === "rect" ? shape.width : shape.radiusX * 2);
    const bottom = shape.y + (shape.kind === "rect" ? shape.height : shape.radiusY * 2);
    return withBox(shape, shape, {
      x: dragEdge(right, shape.x, dx, pixel.x),
      y: dragEdge(bottom, shape.y, dy, pixel.y),
    });
  }
  switch (draft.kind) {
    case "rect":
    case "ellipse":
      return withBox(draft, start, point);
    case "arrow":
      return { ...draft, to: point };
    case "freehand": {
      const last = draft.points.at(-1);
      if (last?.x === point.x && last.y === point.y) return draft;
      const points = bounded && draft.points.length >= MAX_VISIT_STROKE_POINTS
        ? draft.points.filter((_, i) => i % 2 === 0 || i === draft.points.length - 1)
        : draft.points;
      return { ...draft, points: [...points, point] };
    }
    case "text":
      return draft;
  }
}

function hasVisibleMark(shape: LayerShape, pixel: Point): boolean {
  switch (shape.kind) {
    case "rect":
      return shape.width >= pixel.x || shape.height >= pixel.y;
    case "ellipse":
      return shape.radiusX * 2 >= pixel.x || shape.radiusY * 2 >= pixel.y;
    case "arrow":
      return Math.abs(shape.to.x - shape.from.x) >= pixel.x ||
        Math.abs(shape.to.y - shape.from.y) >= pixel.y;
    case "freehand":
      return shape.points.length > 1;
    case "text":
      return true;
  }
}

interface WorkspaceFrame {
  readonly id: string;
  readonly url: string;
  readonly name: string;
  readonly category: string;
}

interface LiveFrame {
  readonly frame: WorkspaceFrame;
  readonly shapes: readonly LayerShape[];
  readonly editable: boolean;
  readonly saveEnabled: boolean;
  readonly onSave: (shapes: readonly LayerShape[]) => void;
  readonly feedback?: ReactNode;
  readonly version?: ReactNode;
}

/**
 * Frames come from the authored sample set for this patient; when the patient
 * has none, the originals already loaded on the compare surface are used
 * instead, so the drawing tools never claim an image the reader cannot see.
 */
export function AnnotationWorkspace({
  patientId,
  photos = [],
  live,
}: {
  patientId: string;
  photos?: readonly Photo[];
  live?: LiveFrame;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { session } = useSession();
  const authoredFrames = live ? [] : sampleFramesFor(patientId);
  const frames: readonly WorkspaceFrame[] = live ? [live.frame] : authoredFrames.length
    ? authoredFrames.map((frame) => ({
        id: frame.id,
        url: frame.url,
        name: frame.originalFile,
        category: frame.category,
      }))
    : photos.map((photo) => ({
        id: photo.id,
        url: photo.url,
        name: photo.id,
        category: photo.category,
      }));
  const hasAuthoredFrames = authoredFrames.length > 0;
  const [frameId, setFrameId] = useState(frames[0]?.id ?? "");
  const [annotation, setAnnotation] = useState<AnnotationState>(() =>
    createState(live?.shapes ?? seedShapes(frameId === "" ? [] : versionsFor(frameId))),
  );
  const [versions, setVersions] = useState<
    Record<string, SavedAnnotationVersion[]>
  >(() =>
    Object.fromEntries(
      live ? [] : frames.map((frame) => [frame.id, seedVersions(versionsFor(frame.id))]),
    ),
  );
  const [textValue, setTextValue] = useState("");
  const [zoom, setZoom] = useState(1);
  const [status, setStatus] = useState<"none" | "textRequired" | "saved">("none");
  const gesture = useRef<Gesture | null>(null);
  const [preview, setPreview] = useState<LayerShape | null>(null);
  const sequence = useRef(0);
  const [photoRatio, setPhotoRatio] = useState<number | null>();

  if (frames.length === 0)
    return (
      <Card title={t("annotation.title")}>
        <SampleBanner label={t("app.demoTag")} title={t("annotation.title")}>
          <p>{t("annotation.sampleBody")}</p>
        </SampleBanner>
        <div className="pc-empty">
          <h3 className="pc-empty__title">{t("annotation.empty")}</h3>
          <p>{t("annotation.emptyBody")}</p>
        </div>
      </Card>
    );

  const frame = frames.find((row) => row.id === frameId) ?? frames[0];
  if (frame === undefined) return null;
  const frameVersions = versions[frame.id] ?? [];
  const frameDrafts = live ? [] : draftsFor(frame.id);
  const editable = live ? Boolean(photoRatio && live.editable) : true;

  function nextId(): string {
    let id: string;
    do { id = `LOCAL-SHAPE-${++sequence.current}`; }
    while (annotation.shapes.some((shape) => shape.id === id) || live?.shapes.some((shape) => shape.id === id));
    return id;
  }

  function changeFrame(id: string) {
    cancelGesture();
    setFrameId(id);
    setAnnotation(createState(seedShapes(versionsFor(id))));
    setStatus("none");
    setZoom(1);
  }

  function pickTool(tool: AnnotationTool) {
    cancelGesture();
    setAnnotation((current) => chooseTool(current, tool));
    setStatus("none");
  }

  function startGesture(point: Point, hit: { id: string; resize: boolean } | null) {
    if (!editable) return false;
    if (annotation.tool === "select") {
      const original = annotation.shapes.find((shape) => shape.id === hit?.id);
      setAnnotation((current) => selectShape(current, original?.id ?? null));
      if (!original) return false;
      const resize = hit?.resize && (original.kind === "rect" || original.kind === "ellipse");
      gesture.current = resize
        ? { kind: "resize", start: point, original, draft: original }
        : { kind: "move", start: point, original, draft: original };
      return true;
    }
    if (live && annotation.shapes.length >= MAX_VISIT_SHAPES) return false;
    const text = textValue.trim();
    if (annotation.tool === "text" && !text) {
      setStatus("textRequired");
      return false;
    }
    setStatus("none");
    const common = { id: nextId(), stroke: "#0f766e" };
    const anchor = { ...common, ...point };
    let draft: LayerShape;
    switch (annotation.tool) {
      case "text":
        draft = { ...anchor, kind: "text", text };
        break;
      case "freehand":
        draft = { ...common, kind: "freehand", points: [point], stroke: "#155e75", strokeWidth: 0.8 };
        break;
      case "arrow":
        draft = { ...common, kind: "arrow", from: point, to: point };
        break;
      case "ellipse":
        draft = { ...anchor, kind: "ellipse", radiusX: 0, radiusY: 0 };
        break;
      case "rect":
        draft = { ...anchor, kind: "rect", width: 0, height: 0 };
        break;
    }
    gesture.current = { kind: "draw", start: point, draft };
    setPreview(draft);
    return true;
  }

  function moveGesture(point: Point, pixel: Point) {
    const active = gesture.current;
    if (!editable || active === null) return;
    active.draft = updateDraft(active, point, pixel, Boolean(live));
    setPreview(active.draft);
  }

  function cancelGesture() {
    gesture.current = null;
    setPreview(null);
  }

  function finishGesture(point: Point, pixel: Point) {
    const active = gesture.current;
    cancelGesture();
    if (!editable || active === null) return;
    const shape = updateDraft(active, point, pixel, Boolean(live));
    if (active.kind === "draw") {
      if (live && (shape.kind === "rect" && (!shape.width || !shape.height) ||
          shape.kind === "ellipse" && (!shape.radiusX || !shape.radiusY))) return;
      if (hasVisibleMark(shape, pixel))
        setAnnotation((current) => addShape(current, shape));
      return;
    }
    if (
      (Math.abs(point.x - active.start.x) < pixel.x &&
        Math.abs(point.y - active.start.y) < pixel.y) ||
      JSON.stringify(shape) === JSON.stringify(active.original)
    ) return;
    setAnnotation((current) =>
      replaceShapes(
        checkpoint(current),
        current.shapes.map((row) => row.id === shape.id ? shape : row),
      ),
    );
  }

  function applyEdit(edit: (current: AnnotationState) => AnnotationState) {
    if (!editable) return;
    cancelGesture();
    setAnnotation(edit);
  }

  const activeFrame = frame;

  function save() {
    if (live) {
      if (photoRatio && live.saveEnabled) {
        cancelGesture();
        live.onSave(annotation.shapes);
      }
      return;
    }
    const at = new Date().toISOString();
    const author = session?.user.id ?? "demo";
    setVersions((current) => ({
      ...current,
      [activeFrame.id]: saveVersion(
        current[activeFrame.id] ?? [],
        annotation.shapes,
        { author, at },
      ),
    }));
    setStatus("saved");
  }

  function loadVersion(version: SavedAnnotationVersion) {
    applyEdit((current) =>
      replaceShapes(checkpoint(selectShape(current, null)), version.shapes),
    );
    setStatus("none");
  }

  // The preview never changes saved layers or history. A completed gesture
  // promotes it to one engine edit; cancel leaves both undo and redo intact.
  const visibleShapes = preview === null
    ? annotation.shapes
    : annotation.shapes.some((shape) => shape.id === preview.id)
      ? annotation.shapes.map((shape) => shape.id === preview.id ? preview : shape)
      : [...annotation.shapes, preview];

  return (
    <Card title={t("annotation.title")}>
      {!live && <SampleBanner label={t("app.demoTag")} title={t("annotation.title")}>
        <p>{t("annotation.sampleBody")}</p>
      </SampleBanner>}

      {hasAuthoredFrames ? (
        <div className="annotation-frame">
          <label htmlFor={`annotation-frame-${patientId}`}>
            {t("annotation.frame")}
          </label>
          <select
            id={`annotation-frame-${patientId}`}
            value={frame.id}
            onChange={(event) => changeFrame(event.target.value)}
          >
            {frames.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} · {t(`photos.category.${row.category}`)}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <p className="annotation-status">
          {t("annotation.frame")}: {frame.name}
          {!live && <> · {t(`photos.category.${frame.category}`)}</>}
        </p>
      )}

      <div className="annotation-toolbar" role="toolbar" aria-label={t("annotation.title")}>
        {ANNOTATION_TOOLS.map((tool) => (
          <Button
            key={tool}
            size="sm"
            variant={annotation.tool === tool ? "cyan" : "secondary"}
            aria-pressed={annotation.tool === tool}
            disabled={!editable}
            onClick={() => pickTool(tool)}
          >
            {t(`annotation.tool.${tool}`)}
          </Button>
        ))}
      </div>

      <div className="annotation-toolbar">
        <Button
          size="sm"
          onClick={() => applyEdit(undo)}
          disabled={!editable || !canUndo(annotation)}
        >
          {t("action.undo")}
        </Button>
        <Button
          size="sm"
          onClick={() => applyEdit(redo)}
          disabled={!editable || !canRedo(annotation)}
        >
          {t("action.redo")}
        </Button>
        <Button
          size="sm"
          onClick={() => applyEdit(deleteSelected)}
          disabled={!editable || annotation.selectedId === null}
        >
          {t("annotation.delete")}
        </Button>
        <Button size="sm" onClick={() => setZoom((current) => Math.min(2, current + 0.25))}>
          {t("annotation.zoom")}
        </Button>
        <Button size="sm" onClick={() => setZoom(1)}>
          {t("action.resetView")}
        </Button>
      </div>

      <div className="annotation-textfield">
        <label htmlFor={`annotation-text-${patientId}`}>
          {t("annotation.textValue")}
        </label>
        <input
          id={`annotation-text-${patientId}`}
          value={textValue}
          disabled={!editable}
          maxLength={live ? MAX_VISIT_TEXT : undefined}
          onChange={(event) => setTextValue(event.target.value)}
        />
      </div>

      <div className={live ? "annotation-stage annotation-stage--visit" : "annotation-stage"}>
        <div
          className="annotation-stage__inner"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "center",
            ...(live && { "--annotation-photo-ratio": photoRatio ?? 1 }),
          } as CSSProperties}
        >
          <img src={frame.url} alt={frame.name} draggable={false}
            onLoad={live ? ({ currentTarget: { naturalWidth, naturalHeight } }) =>
              setPhotoRatio(naturalWidth && naturalHeight ? naturalWidth / naturalHeight : null) : undefined}
            onError={live ? () => setPhotoRatio(null) : undefined} />
          {(!live || photoRatio) && <KonvaAnnotationSurface
            label={t("annotation.canvas")}
            shapes={visibleShapes}
            selectedId={annotation.selectedId}
            selecting={annotation.tool === "select"}
            zoom={zoom}
            onStart={startGesture}
            onMove={moveGesture}
            onFinish={finishGesture}
            onCancel={cancelGesture}
            onDelete={() => applyEdit(deleteSelected)}
          />}
        </div>
      </div>
      {live && photoRatio === null && <p className="annotation-status" role="status">
        {t("workspace.visitPhotos.imageUnavailable")}
      </p>}

      <div className="annotation-save">
        <Button variant="primary" size="sm" onClick={save} disabled={live && (!photoRatio || !live.saveEnabled)}>
          {t("action.saveVersion")}
        </Button>
        <p className="annotation-status" role="status">
          {!live && <>{t("annotation.versions", { count: frameVersions.length })} ·{" "}</>}
          {t("annotation.shapes", { count: annotation.shapes.length })}
        </p>
      </div>
      {live?.feedback}
      {status === "textRequired" && (
        <p className="annotation-status" role="alert">
          {t("annotation.textRequired")}
        </p>
      )}
      {status === "saved" && (
        <p className="annotation-status" role="status">
          {t("annotation.saved")}
        </p>
      )}

      <section className="annotation-layers" aria-label={t("annotation.layers")}>
        <h3>{t("annotation.layers")}</h3>
        {frameDrafts.map((draft) => (
          <p key={draft.id} className="annotation-layer">
            <Badge tone="demo">{t("annotation.draft")}</Badge>{" "}
            {draft.id} · {t("annotation.version", { number: draft.currentVersionNumber })} ·{" "}
            {formatDateTime(draft.updatedAt, locale)}
          </p>
        ))}
        <ul>
          {live?.version}
          {[...frameVersions].reverse().map((version) => (
            <li key={version.id} className="annotation-layer">
              <Badge tone={version.state === "pending" ? "warning" : "success"}>
                {t(`annotation.state.${version.state}`)}
              </Badge>{" "}
              {t("annotation.version", { number: version.versionNumber })} ·{" "}
              {t("annotation.shapes", { count: version.shapes.length })} ·{" "}
              {version.author} · {formatDate(version.createdAt, locale)}{" "}
              <Button size="sm" onClick={() => loadVersion(version)}>
                {t("annotation.openVersion")}
              </Button>
            </li>
          ))}
        </ul>
      </section>
    </Card>
  );
}
