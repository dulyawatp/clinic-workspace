import { act, fireEvent, screen } from "@testing-library/react";
import { forwardRef, type ReactNode, useImperativeHandle } from "react";
import { expect, vi } from "vitest";

// jsdom has no canvas renderer. Stub only the Konva boundary, leaving the
// actual surface, hit-coordinate adapter, workspace and engine under test.
function mark() {
  return vi.fn((_props: {
    id: string;
    name?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    radiusX?: number;
    radiusY?: number;
    points?: number[];
    text?: string;
  }) => null);
}

export const konva = {
  getIntersection: vi.fn(),
  Group: vi.fn(),
  Rect: mark(),
  Ellipse: mark(),
  Arrow: mark(),
  Line: mark(),
  Text: mark(),
};

export const { Rect, Ellipse, Arrow, Line, Text } = konva;

export const Stage = forwardRef(function Stage(
  { children, width, height }: { children: ReactNode; width: number; height: number },
  ref,
) {
  useImperativeHandle(ref, () => ({ getIntersection: konva.getIntersection }));
  return <div><canvas width={width} height={height} />{children}</div>;
});

export const Layer = ({ children }: { children: ReactNode }) => children;

export function Group(
  { children, ...props }: { children: ReactNode; scaleX: number; scaleY: number },
) {
  konva.Group(props);
  return children;
}

let bounds: DOMRect;
let notifyResize: ResizeObserverCallback;

export function mockKonvaLayout() {
  vi.clearAllMocks();
  konva.getIntersection.mockReset();
  bounds = new DOMRect(0, 0, 800, 500);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(500);
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) { notifyResize = callback; }
    observe() {}
    disconnect() {}
  });
}

export function resizeStage(width: number, height: number, zoomedBounds: DOMRect) {
  bounds = zoomedBounds;
  act(() => notifyResize(
    [{ contentRect: new DOMRect(0, 0, width, height) } as ResizeObserverEntry],
    {} as ResizeObserver,
  ));
}

export async function getSurface() {
  const surface = await screen.findByRole("application", { name: "พื้นที่วาดกำกับ" });
  vi.spyOn(surface, "getBoundingClientRect").mockImplementation(() => bounds);
  return surface;
}

export function pointer([clientX, clientY]: [number, number], pointerType = "mouse") {
  return { clientX, clientY, pointerId: 1, pointerType, isPrimary: true, button: 0 };
}

export function draw(surface: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(surface, pointer(from));
  fireEvent.pointerMove(surface, pointer(to));
  fireEvent.pointerUp(surface, pointer(to));
}

export function expectCount(count: number) {
  expect(screen.getByText(new RegExp(`เวอร์ชันที่บันทึก: \\d+ · ${count} รูปทรง`))).toBeVisible();
}
