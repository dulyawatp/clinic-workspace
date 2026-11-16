import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
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
import { draftsFor, sampleFramesFor, versionsFor } from "./sample-frames";
import "./annotate.css";

const VIEW = 100;

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

/**
 * Pointer → normalized 0..1 coordinates over the stage box, not the visible
 * image: the original is `object-fit: contain`, so its letterboxing is
 * deliberately ignored and shapes stay on the stage grid. jsdom has no layout,
 * so a fixed 800×500 fallback keeps the conversion deterministic.
 */
function pointOf(
  event: { clientX: number; clientY: number },
  element: Element,
): Point {
  const rect = element.getBoundingClientRect();
  const width = rect.width || 800;
  const height = rect.height || 500;
  return {
    x: clamp((event.clientX - rect.left) / width),
    y: clamp((event.clientY - rect.top) / height),
  };
}

function shapeOffset(shape: LayerShape, dx: number, dy: number): LayerShape {
  switch (shape.kind) {
    case "rect":
    case "ellipse":
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

type Gesture =
  | {
      readonly kind: "move";
      readonly id: string;
      readonly start: Point;
      readonly original: LayerShape;
    }
  | { readonly kind: "draw"; readonly id: string; readonly start: Point }
  | { readonly kind: "freehand"; readonly id: string };

interface WorkspaceFrame {
  readonly id: string;
  readonly url: string;
  readonly name: string;
  readonly category: string;
}

/**
 * Frames come from the authored sample set for this patient; when the patient
 * has none, the originals already loaded on the compare surface are used
 * instead, so the drawing tools never claim an image the reader cannot see.
 */
export function AnnotationWorkspace({
  patientId,
  photos = [],
}: {
  patientId: string;
  photos?: readonly Photo[];
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { session } = useSession();
  const authoredFrames = sampleFramesFor(patientId);
  const frames: readonly WorkspaceFrame[] = authoredFrames.length
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
    createState(seedShapes(frameId === "" ? [] : versionsFor(frameId))),
  );
  const [versions, setVersions] = useState<
    Record<string, SavedAnnotationVersion[]>
  >(() =>
    Object.fromEntries(
      frames.map((frame) => [frame.id, seedVersions(versionsFor(frame.id))]),
    ),
  );
  const [textValue, setTextValue] = useState("");
  const [zoom, setZoom] = useState(1);
  const [status, setStatus] = useState<"none" | "textRequired" | "saved">("none");
  const gesture = useRef<Gesture | null>(null);
  const sequence = useRef(0);

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
  const frameDrafts = draftsFor(frame.id);

  function nextId(): string {
    sequence.current += 1;
    return `LOCAL-SHAPE-${sequence.current}`;
  }

  function changeFrame(id: string) {
    setFrameId(id);
    setAnnotation(createState(seedShapes(versionsFor(id))));
    setStatus("none");
    setZoom(1);
  }

  function pickTool(tool: AnnotationTool) {
    setAnnotation((current) => chooseTool(current, tool));
    setStatus("none");
  }

  function startPoint(event: ReactPointerEvent<SVGSVGElement>): Point {
    return pointOf(event, event.currentTarget);
  }

  function onPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    const svg = event.currentTarget;
    if (typeof svg.setPointerCapture === "function")
      svg.setPointerCapture(event.pointerId);
    const point = startPoint(event);
    const target = event.target as Element | null;
    const hit = target?.closest("[data-shape-id]")?.getAttribute("data-shape-id");
    if (annotation.tool === "select") {
      const original = annotation.shapes.find((shape) => shape.id === hit);
      // One checkpoint at gesture start: a drag is a single undo step.
      setAnnotation((current) =>
        hit
          ? checkpoint(selectShape(current, hit))
          : selectShape(current, null),
      );
      gesture.current =
        hit && original
          ? { kind: "move", id: hit, start: point, original }
          : null;
      return;
    }
    if (annotation.tool === "text") {
      if (textValue.trim() === "") {
        setStatus("textRequired");
        return;
      }
      const id = nextId();
      setAnnotation((current) =>
        addShape(current, {
          id,
          kind: "text",
          x: point.x,
          y: point.y,
          text: textValue.trim(),
          stroke: "#0f766e",
        }),
      );
      return;
    }
    const id = nextId();
    gesture.current =
      annotation.tool === "freehand"
        ? { kind: "freehand", id }
        : { kind: "draw", id, start: point };
    setAnnotation((current) => {
      if (annotation.tool === "freehand")
        return addShape(current, {
          id,
          kind: "freehand",
          points: [point],
          strokeWidth: 0.8,
          stroke: "#155e75",
        });
      if (annotation.tool === "arrow")
        return addShape(current, {
          id,
          kind: "arrow",
          from: point,
          to: point,
          stroke: "#0f766e",
        });
      if (annotation.tool === "ellipse")
        return addShape(current, {
          id,
          kind: "ellipse",
          x: point.x,
          y: point.y,
          radiusX: 0,
          radiusY: 0,
          stroke: "#0f766e",
        });
      return addShape(current, {
        id,
        kind: "rect",
        x: point.x,
        y: point.y,
        width: 0,
        height: 0,
        stroke: "#0f766e",
      });
    });
  }

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const active = gesture.current;
    if (active === null) return;
    const point = startPoint(event);
    setAnnotation((current) =>
      replaceShapes(
        current,
        current.shapes.map((shape) => {
          if (shape.id !== active.id) return shape;
          if (active.kind === "move")
            return shapeOffset(
              active.original,
              point.x - active.start.x,
              point.y - active.start.y,
            );
          if (active.kind === "freehand" && shape.kind === "freehand")
            return { ...shape, points: [...shape.points, point] };
          if (active.kind !== "draw") return shape;
          if (shape.kind === "arrow")
            return { ...shape, to: point };
          if (shape.kind === "ellipse")
            return {
              ...shape,
              x: Math.min(active.start.x, point.x),
              y: Math.min(active.start.y, point.y),
              radiusX: Math.abs(point.x - active.start.x) / 2,
              radiusY: Math.abs(point.y - active.start.y) / 2,
            };
          if (shape.kind === "rect")
            return {
              ...shape,
              x: Math.min(active.start.x, point.x),
              y: Math.min(active.start.y, point.y),
              width: Math.abs(point.x - active.start.x),
              height: Math.abs(point.y - active.start.y),
            };
          return shape;
        }),
      ),
    );
  }

  function onPointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    const active = gesture.current;
    gesture.current = null;
    // A click without a drag must not leave a zero-size shape or a history
    // entry behind: below one stage pixel the gesture is discarded whole.
    if (active === null || active.kind !== "draw") return;
    const rect = event.currentTarget.getBoundingClientRect();
    const minX = 1 / (rect.width || 800);
    const minY = 1 / (rect.height || 500);
    setAnnotation((current) => {
      const shape = current.shapes.find((row) => row.id === active.id);
      if (shape === undefined) return current;
      const tiny =
        shape.kind === "rect"
          ? shape.width < minX && shape.height < minY
          : shape.kind === "ellipse"
            ? shape.radiusX * 2 < minX && shape.radiusY * 2 < minY
            : shape.kind === "arrow"
              ? Math.abs(shape.to.x - shape.from.x) < minX &&
                Math.abs(shape.to.y - shape.from.y) < minY
              : false;
      if (!tiny) return current;
      const previous = current.past.at(-1);
      if (previous === undefined) return current;
      return {
        ...current,
        shapes: previous,
        past: current.past.slice(0, -1),
        selectedId: null,
      };
    });
  }

  const activeFrame = frame;

  function save() {
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
    setAnnotation((current) =>
      replaceShapes(checkpoint(current), version.shapes),
    );
    setStatus("none");
  }

  return (
    <Card title={t("annotation.title")}>
      <SampleBanner label={t("app.demoTag")} title={t("annotation.title")}>
        <p>{t("annotation.sampleBody")}</p>
      </SampleBanner>

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
          {t("annotation.frame")}: {frame.name} ·{" "}
          {t(`photos.category.${frame.category}`)}
        </p>
      )}

      <div className="annotation-toolbar" role="toolbar" aria-label={t("annotation.title")}>
        {ANNOTATION_TOOLS.map((tool) => (
          <Button
            key={tool}
            size="sm"
            variant={annotation.tool === tool ? "cyan" : "secondary"}
            aria-pressed={annotation.tool === tool}
            onClick={() => pickTool(tool)}
          >
            {t(`annotation.tool.${tool}`)}
          </Button>
        ))}
      </div>

      <div className="annotation-toolbar">
        <Button
          size="sm"
          onClick={() => setAnnotation((current) => undo(current))}
          disabled={!canUndo(annotation)}
        >
          {t("action.undo")}
        </Button>
        <Button
          size="sm"
          onClick={() => setAnnotation((current) => redo(current))}
          disabled={!canRedo(annotation)}
        >
          {t("action.redo")}
        </Button>
        <Button
          size="sm"
          onClick={() => setAnnotation((current) => deleteSelected(current))}
          disabled={annotation.selectedId === null}
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
          onChange={(event) => setTextValue(event.target.value)}
        />
      </div>

      <div className="annotation-stage">
        <div
          className="annotation-stage__inner"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "center",
          }}
        >
          <img src={frame.url} alt={frame.name} />
          <svg
            viewBox={`0 0 ${VIEW} ${VIEW}`}
            preserveAspectRatio="none"
            role="application"
            aria-label={t("annotation.canvas")}
            tabIndex={0}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onKeyDown={(event) => {
              if (event.key === "Delete")
                setAnnotation((current) => deleteSelected(current));
            }}
          >
            {annotation.shapes.map((shape) => {
              const selected = shape.id === annotation.selectedId;
              const className = selected ? "annotation-shape is-selected" : "annotation-shape";
              const common = {
                "data-shape-id": shape.id,
                className,
                stroke: shape.stroke,
                fill: "none",
              } as const;
              if (shape.kind === "rect")
                return (
                  <rect
                    key={shape.id}
                    {...common}
                    strokeWidth={0.8}
                    x={shape.x * VIEW}
                    y={shape.y * VIEW}
                    width={shape.width * VIEW}
                    height={shape.height * VIEW}
                  />
                );
              if (shape.kind === "ellipse")
                return (
                  <ellipse
                    key={shape.id}
                    {...common}
                    strokeWidth={0.8}
                    cx={(shape.x + shape.radiusX) * VIEW}
                    cy={(shape.y + shape.radiusY) * VIEW}
                    rx={shape.radiusX * VIEW}
                    ry={shape.radiusY * VIEW}
                  />
                );
              if (shape.kind === "arrow")
                return (
                  <line
                    key={shape.id}
                    {...common}
                    strokeWidth={0.8}
                    markerEnd="url(#annotation-arrowhead)"
                    x1={shape.from.x * VIEW}
                    y1={shape.from.y * VIEW}
                    x2={shape.to.x * VIEW}
                    y2={shape.to.y * VIEW}
                  />
                );
              if (shape.kind === "freehand")
                return (
                  <polyline
                    key={shape.id}
                    {...common}
                    strokeWidth={shape.strokeWidth}
                    points={shape.points
                      .map((point) => `${point.x * VIEW},${point.y * VIEW}`)
                      .join(" ")}
                  />
                );
              return (
                <text
                  key={shape.id}
                  {...common}
                  strokeWidth={0}
                  fill={shape.stroke}
                  fontSize={4}
                  x={shape.x * VIEW}
                  y={shape.y * VIEW}
                >
                  {shape.text}
                </text>
              );
            })}
            <defs>
              <marker
                id="annotation-arrowhead"
                markerWidth="8"
                markerHeight="8"
                refX="6"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L0,6 L6,3 z" fill="currentColor" />
              </marker>
            </defs>
          </svg>
        </div>
      </div>

      <div className="annotation-save">
        <Button variant="primary" size="sm" onClick={save}>
          {t("action.saveVersion")}
        </Button>
        <p className="annotation-status" role="status">
          {t("annotation.versions", { count: frameVersions.length })} ·{" "}
          {t("annotation.shapes", { count: annotation.shapes.length })}
        </p>
      </div>
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
