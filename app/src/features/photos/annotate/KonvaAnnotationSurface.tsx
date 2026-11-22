import type Konva from "konva";
import { type PointerEvent, useLayoutEffect, useRef, useState } from "react";
import {
  Arrow,
  Ellipse,
  Group,
  Layer,
  Line,
  Rect,
  Stage,
  Text,
} from "react-konva";
import type { LayerShape, Point } from "./engine";

const VIEW = 100;
const RESIZE_HANDLE = "annotation-resize";
const HANDLE_SIZE = 10;
const HANDLE_HIT_STROKE = 18;
const HANDLE_HIT_RADIUS = (HANDLE_SIZE + HANDLE_HIT_STROKE) / 2;
const MOVE_CLEARANCE = HANDLE_HIT_RADIUS + 4;
const HANDLE_GAP = HANDLE_HIT_RADIUS + 8;

type SurfaceHit = { id: string; resize: boolean } | null;

function resizeTarget(
  origin: Point,
  mark: { width: number; height: number },
  stage: { width: number; height: number },
  zoom: number,
): Point | null {
  const right = origin.x + mark.width;
  const bottom = origin.y + mark.height;
  if (mark.width >= MOVE_CLEARANCE || mark.height >= MOVE_CLEARANCE)
    return { x: right, y: bottom };

  // The centered CSS zoom crops the stage. Pick a side with enough room for
  // the whole hit target and leave a clear gap between that target and mark.
  const cropX = (stage.width - stage.width / zoom) / 2;
  const cropY = (stage.height - stage.height / zoom) / 2;
  const farX = stage.width - cropX;
  const farY = stage.height - cropY;
  if (right < cropX || origin.x > farX || bottom < cropY || origin.y > farY)
    return null;
  const minX = cropX + HANDLE_HIT_RADIUS;
  const minY = cropY + HANDLE_HIT_RADIUS;
  const maxX = farX - HANDLE_HIT_RADIUS;
  const maxY = farY - HANDLE_HIT_RADIUS;
  if (minX > maxX || minY > maxY) return null;
  const middleX = Math.max(minX, Math.min(maxX, origin.x + mark.width / 2));
  const middleY = Math.max(minY, Math.min(maxY, origin.y + mark.height / 2));
  const options = [
    { x: right + HANDLE_GAP, y: middleY, room: maxX - right },
    { x: origin.x - HANDLE_GAP, y: middleY, room: origin.x - minX },
    { x: middleX, y: bottom + HANDLE_GAP, room: maxY - bottom },
    { x: middleX, y: origin.y - HANDLE_GAP, room: origin.y - minY },
  ];
  const best = options.reduce((current, next) => next.room > current.room ? next : current);
  return best.room >= HANDLE_GAP ? { x: best.x, y: best.y } : null;
}

function renderShape(shape: LayerShape, selected: boolean) {
  const style = {
    id: shape.id,
    stroke: shape.stroke,
    strokeWidth: selected
      ? 1.4
      : shape.kind === "text" ? 0.8 : shape.strokeWidth ?? 0.8,
    dash: selected ? [4, 3] : [],
  };
  switch (shape.kind) {
    case "rect":
      return (
        <Rect
          key={shape.id}
          {...style}
          x={shape.x * VIEW}
          y={shape.y * VIEW}
          width={shape.width * VIEW}
          height={shape.height * VIEW}
        />
      );
    case "ellipse":
      return (
        <Ellipse
          key={shape.id}
          {...style}
          x={(shape.x + shape.radiusX) * VIEW}
          y={(shape.y + shape.radiusY) * VIEW}
          radiusX={shape.radiusX * VIEW}
          radiusY={shape.radiusY * VIEW}
        />
      );
    case "arrow":
      return (
        <Arrow
          key={shape.id}
          {...style}
          fill={shape.stroke}
          points={[
            shape.from.x * VIEW, shape.from.y * VIEW,
            shape.to.x * VIEW, shape.to.y * VIEW,
          ]}
          pointerLength={4.8}
          pointerWidth={4.8}
        />
      );
    case "freehand":
      return (
        <Line
          key={shape.id}
          {...style}
          points={shape.points.flatMap(({ x, y }) => [x * VIEW, y * VIEW])}
        />
      );
    case "text":
      return (
        <Text
          key={shape.id}
          id={shape.id}
          x={shape.x * VIEW}
          y={shape.y * VIEW}
          offsetY={4}
          fontSize={shape.fontSize ?? 4}
          fontFamily="Inter, IBM Plex Sans Thai, Sukhumvit Set, Thonburi, Noto Sans Thai, sans-serif"
          fill={shape.stroke}
          text={shape.text}
          stroke={selected ? shape.stroke : undefined}
          strokeWidth={selected ? 0.2 : 0}
        />
      );
  }
}

export function KonvaAnnotationSurface({
  label,
  shapes,
  selectedId,
  selecting,
  zoom,
  onStart,
  onMove,
  onFinish,
  onCancel,
  onDelete,
}: {
  label: string;
  shapes: readonly LayerShape[];
  selectedId: string | null;
  selecting: boolean;
  zoom: number;
  onStart: (point: Point, hit: SurfaceHit) => boolean;
  onMove: (point: Point, pixel: Point) => void;
  onFinish: (point: Point, pixel: Point) => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const pointer = useRef<{ id: number; bounds: DOMRect } | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [resizeOffset, setResizeOffset] = useState<Point | null>(null);
  const { width, height } = size;

  useLayoutEffect(() => {
    const node = surface.current;
    if (!node) return;
    setSize({ width: node.clientWidth, height: node.clientHeight });
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) {
        const { width, height } = entry.contentRect;
        setSize({ width, height });
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // The bounding box includes CSS zoom; the Konva buffer does not. Both use
  // the same surface (the picture for visits, the full stage for fixtures).
  function position(event: PointerEvent<HTMLDivElement>, box: DOMRect) {
    if (!box.width || !box.height) return null;
    const clamp = (n: number) => Math.max(0, Math.min(1, n));
    return {
      point: {
        x: clamp((event.clientX - box.left) / box.width),
        y: clamp((event.clientY - box.top) / box.height),
      },
      pixel: { x: 1 / box.width, y: 1 / box.height },
    };
  }

  function start(event: PointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0 || pointer.current !== null) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const at = position(event, bounds);
    if (!at) return;
    event.currentTarget.focus({ preventScroll: true });
    const node = selecting
      ? stage.current?.getIntersection({ x: at.point.x * width, y: at.point.y * height })
      : null;
    const id = node?.id();
    const hit = id && node ? { id, resize: node.hasName(RESIZE_HANDLE) } : null;
    if (!onStart(at.point, hit)) return;
    // Keep the offset target attached during resize even if the shape grows
    // out of the tiny range before the gesture finishes.
    if (hit?.resize && handle)
      setResizeOffset({ x: handle.x - handle.corner.x, y: handle.y - handle.corner.y });
    pointer.current = { id: event.pointerId, bounds };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function update(event: PointerEvent<HTMLDivElement>, finished: boolean) {
    const active = pointer.current;
    if (!active || active.id !== event.pointerId) return;
    const box = event.currentTarget.getBoundingClientRect();
    const { bounds } = active;
    // Don't interpret layout or CSS zoom motion as pointer motion.
    if (
      bounds.left !== box.left || bounds.top !== box.top ||
      bounds.width !== box.width || bounds.height !== box.height
    ) {
      cancelCapture(event.currentTarget);
      return;
    }
    const at = position(event, bounds);
    if (!at) return;
    if (finished) {
      pointer.current = null;
      setResizeOffset(null);
      onFinish(at.point, at.pixel);
    } else {
      onMove(at.point, at.pixel);
    }
  }

  function cancel(pointerId: number) {
    if (pointer.current?.id !== pointerId) return;
    pointer.current = null;
    setResizeOffset(null);
    onCancel();
  }

  function cancelCapture(node: HTMLDivElement) {
    const id = pointer.current?.id;
    if (id === undefined) return;
    cancel(id);
    if (node.hasPointerCapture?.(id)) node.releasePointerCapture(id);
  }

  const selected = selecting
    ? shapes.find((shape) => shape.id === selectedId)
    : undefined;
  let handle: (Point & { id: string; corner: Point }) | null = null;
  if (selected?.kind === "rect" || selected?.kind === "ellipse") {
    const origin = { x: selected.x * width, y: selected.y * height };
    const mark = {
      width: (selected.kind === "rect" ? selected.width : selected.radiusX * 2) * width,
      height: (selected.kind === "rect" ? selected.height : selected.radiusY * 2) * height,
    };
    const corner = { x: origin.x + mark.width, y: origin.y + mark.height };
    const target = resizeOffset
      ? { x: corner.x + resizeOffset.x, y: corner.y + resizeOffset.y }
      : resizeTarget(origin, mark, size, zoom);
    if (target) handle = { id: selected.id, corner, ...target };
  }

  return (
    <div
      ref={surface}
      className="annotation-stage__surface"
      role="application"
      aria-label={label}
      tabIndex={0}
      onPointerDown={start}
      onPointerMove={(event) => update(event, false)}
      onPointerUp={(event) => update(event, true)}
      onPointerCancel={(event) => cancel(event.pointerId)}
      onLostPointerCapture={(event) => cancel(event.pointerId)}
      onKeyDown={(event) => {
        if (event.key === "Escape") cancelCapture(event.currentTarget);
        if (event.key === "Delete") {
          event.preventDefault();
          if (event.repeat) return;
          if (pointer.current) cancelCapture(event.currentTarget);
          else onDelete();
        }
      }}
    >
      <Stage ref={stage} width={width} height={height} aria-hidden="true">
        <Layer listening={selecting}>
          <Group scaleX={width / VIEW} scaleY={height / VIEW}>
            {shapes.map((shape) => renderShape(shape, shape.id === selectedId))}
          </Group>
          {handle && (handle.x !== handle.corner.x || handle.y !== handle.corner.y) && (
            <Line
              points={[handle.corner.x, handle.corner.y, handle.x, handle.y]}
              stroke="#0f766e"
              strokeWidth={1}
              dash={[3, 3]}
              listening={false}
            />
          )}
          {handle && (
            <Rect
              id={handle.id}
              name={RESIZE_HANDLE}
              x={handle.x - HANDLE_SIZE / 2}
              y={handle.y - HANDLE_SIZE / 2}
              width={HANDLE_SIZE}
              height={HANDLE_SIZE}
              fill="#fff"
              stroke="#0f766e"
              strokeWidth={2}
              hitStrokeWidth={HANDLE_HIT_STROKE}
            />
          )}
        </Layer>
      </Stage>
    </div>
  );
}
