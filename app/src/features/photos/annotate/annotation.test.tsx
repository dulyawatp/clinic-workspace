import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { server } from "../../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../../test/render";
import { AnnotationWorkspace } from "./AnnotationWorkspace";
import { draw, expectCount, getSurface, konva, mockKonvaLayout, pointer, resizeStage } from "./konva-test-support";

vi.mock("react-konva", () => import("./konva-test-support"));

const rectId = "SAMPLE-ANV-3001-2-1";
const ellipseId = "SAMPLE-ANV-3001-2-2";

beforeEach(mockKonvaLayout);
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderWorkspace(patientId = "P-10241") {
  return renderWithFixtures(<AnnotationWorkspace patientId={patientId} />);
}

function hit(id: string, resize = false) {
  konva.getIntersection.mockReturnValueOnce({ id: () => id, hasName: () => resize });
}

function renderedRect(id: string) {
  return konva.Rect.mock.calls.filter(([mark]) => mark.id === id && !mark.name).at(-1)?.[0];
}

it("renders a canvas inside the named keyboard drawing surface", async () => {
  renderWorkspace();
  const surface = await getSurface();
  expect(surface).toHaveAttribute("tabindex", "0");
  expect(surface.querySelector("canvas")).toBeInTheDocument();
});

it("seeds the authored sample layers and lists versions and drafts", async () => {
  renderWorkspace();
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  await getSurface();
  // Latest authored version for the before frame has four shapes.
  expect(renderedRect(rectId)).toBeDefined();
  expect(konva.Ellipse).toHaveBeenCalled();
  expect(konva.Line).toHaveBeenCalled();
  expect(konva.Arrow).toHaveBeenCalled();
  expect(screen.getByText(/เวอร์ชันที่บันทึก: 2 · 4 รูปทรง/)).toBeVisible();
  const layers = screen.getByRole("region", { name: "เลเยอร์และฉบับร่าง" });
  expect(within(layers).getByText(/SAMPLE-PHD-3001/)).toBeVisible();
  const rows = within(layers).getAllByRole("listitem");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent(/เวอร์ชัน 2/);
  expect(rows[1]).toHaveTextContent(/เวอร์ชัน 1/);
  expect(within(layers).getByText("รอตรวจ")).toBeVisible();
});

it("draws an arrow, then undoes and redoes it deterministically", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  expectCount(4);

  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [100, 100], [220, 160]);
  expectCount(5);
  const arrow = konva.Arrow.mock.lastCall?.[0];
  expect(arrow?.points).toEqual([12.5, 20, expect.closeTo(27.5), 32]);

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expectCount(4);

  await user.click(screen.getByRole("button", { name: "ทำซ้ำ" }));
  expectCount(5);
  expect(konva.Arrow.mock.lastCall?.[0].points).toEqual(arrow?.points);

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "ทำซ้ำ" })).toBeEnabled();
});

it("records a freehand stroke as one undoable layer step", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  await user.click(screen.getByRole("button", { name: "วาดอิสระ" }));
  fireEvent.pointerDown(surface, pointer([50, 50]));
  fireEvent.pointerMove(surface, pointer([80, 70]));
  fireEvent.pointerMove(surface, pointer([120, 90]));
  fireEvent.pointerUp(surface, pointer([120, 90]));
  expectCount(5);
  expect(konva.Line.mock.lastCall?.[0].points).toHaveLength(6);
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expectCount(4);
  expect(konva.Line.mock.lastCall?.[0].id).toBe("SAMPLE-ANV-3001-2-3");
});

it("requires annotation text before placing a label", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  await user.click(screen.getByRole("button", { name: "ข้อความ" }));
  draw(surface, [100, 100], [100, 100]);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "กรอกข้อความกำกับก่อน",
  );
  expectCount(4);

  await user.type(screen.getByLabelText("ข้อความกำกับ"), "รอยแดง");
  draw(surface, [100, 100], [100, 100]);
  expectCount(5);
  expect(konva.Text.mock.lastCall?.[0]).toMatchObject({ text: "รอยแดง", x: 12.5, y: 20 });
});

it("deletes the selected shape and can undo the delete", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  hit(rectId);
  draw(surface, [240, 100], [240, 100]);
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
  expect(surface).toHaveFocus();
  const remove = screen.getByRole("button", { name: "ลบที่เลือก" });
  expect(remove).toBeEnabled();
  await user.keyboard("{Delete}");
  expectCount(3);
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expectCount(4);
});

it("saves and reopens equivalent rectangle geometry without any transport call", async () => {
  const user = userEvent.setup();
  const writes = vi.fn();
  server.events.on("request:start", (event) => {
    if (event.request.method !== "GET") writes(event.request.url);
  });
  renderWorkspace();
  const surface = await getSurface();
  const original = screen.getByRole("img");
  const originalSrc = original.getAttribute("src");
  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  draw(surface, [300, 200], [100, 100]);
  const rectangle = renderedRect("LOCAL-SHAPE-1");
  const geometry = { x: 12.5, y: 20, width: 25, height: 20 };
  expect(rectangle).toMatchObject(geometry);
  await user.click(
    screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" }),
  );
  expect(
    await screen.findByText("บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน"),
  ).toBeVisible();
  expect(screen.getByText(/เวอร์ชันที่บันทึก: 3/)).toBeVisible();
  const layers = screen.getByRole("region", { name: "เลเยอร์และฉบับร่าง" });
  const saved = within(layers).getAllByRole("listitem");
  expect(saved[0]).toHaveTextContent(/เวอร์ชัน 3/);
  await user.click(within(saved[1]!).getByRole("button", { name: "เปิดเวอร์ชันนี้" }));
  expectCount(4);
  konva.Rect.mockClear();
  await user.click(within(saved[0]!).getByRole("button", { name: "เปิดเวอร์ชันนี้" }));
  expectCount(5);
  expect(renderedRect("LOCAL-SHAPE-1")).toMatchObject(geometry);
  expect(original).toHaveAttribute("src", originalSrc);
  expect(writes).not.toHaveBeenCalled();
  server.events.removeAllListeners("request:start");
});

it("opens an older version without touching the original or the stored rows", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  await screen.findByLabelText("พื้นที่วาดกำกับ");
  const layers = screen.getByRole("region", { name: "เลเยอร์และฉบับร่าง" });
  const open = within(layers).getAllByRole("button", {
    name: "เปิดเวอร์ชันนี้",
  });
  await user.click(open[open.length - 1] as HTMLElement);
  expectCount(2);
  expect(within(layers).getAllByRole("listitem")).toHaveLength(2);
});

it("shows the designed empty state when the patient has no authored original", async () => {
  renderWorkspace("fixture-patient-01");
  expect(
    await screen.findByText("ยังไม่มีต้นฉบับสำหรับวาดกำกับ"),
  ).toBeVisible();
  expect(screen.queryByLabelText("พื้นที่วาดกำกับ")).toBeNull();
});

it("keeps the annotation controls reachable at 390px", async () => {
  setViewport(390);
  renderWorkspace();
  expect(
    await screen.findByLabelText("พื้นที่วาดกำกับ"),
  ).toBeVisible();
  const toolbar = screen.getAllByRole("toolbar")[0];
  expect(toolbar?.querySelectorAll("button").length).toBe(6);
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeVisible();
});

it("undoes a drag as one step and restores the pre-move shapes", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const before = renderedRect(rectId);
  expect(before).toMatchObject({ x: 30, y: 16 });
  hit(rectId);
  draw(surface, [240, 100], [340, 175]);
  expect(renderedRect(rectId)).toMatchObject({ x: 42.5, y: expect.closeTo(31) });

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(renderedRect(rectId)).toMatchObject({ x: before?.x, y: before?.y });
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
});

it("discards a click that draws no shape", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  draw(surface, [50, 50], [50, 50]);
  expectCount(4);
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
});

it("draws and resizes an ellipse with touch, recording resize as one edit", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  await user.click(screen.getByRole("button", { name: "วงรี" }));
  fireEvent.pointerDown(surface, pointer([100, 100], "touch"));
  fireEvent.pointerMove(surface, pointer([300, 250], "touch"));
  fireEvent.pointerUp(surface, pointer([300, 250], "touch"));
  expectCount(5);
  const geometry = { x: 25, y: 35, radiusX: 12.5, radiusY: 15 };
  expect(konva.Ellipse.mock.lastCall?.[0]).toMatchObject(geometry);
  await user.click(screen.getByRole("button", { name: "เลือก" }));
  expect(konva.Rect.mock.lastCall?.[0]).toMatchObject({ name: "annotation-resize", x: 295, y: 245 });
  hit("LOCAL-SHAPE-1", true);
  fireEvent.pointerDown(surface, pointer([300, 250], "touch"));
  fireEvent.pointerMove(surface, pointer([350, 275], "touch"));
  fireEvent.pointerUp(surface, pointer([400, 300], "touch"));
  expectCount(5);
  expect(konva.Ellipse.mock.lastCall?.[0]).toMatchObject({
    x: 31.25, y: 40, radiusX: 18.75, radiusY: 20,
  });
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(konva.Ellipse.mock.lastCall?.[0]).toMatchObject(geometry);
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expectCount(4);
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
});

it("rolls back canceled drawing, movement and resize without deleting redo", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const undo = screen.getByRole("button", { name: "ย้อนกลับ" });
  const redo = screen.getByRole("button", { name: "ทำซ้ำ" });
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [100, 100], [200, 200]);
  await user.click(undo);

  fireEvent.pointerDown(surface, pointer([100, 100], "touch"));
  fireEvent.pointerMove(surface, pointer([220, 160], "touch"));
  fireEvent.pointerCancel(surface, pointer([220, 160], "touch"));
  expectCount(4);
  expect(undo).toBeDisabled();
  expect(redo).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "เลือก" }));
  const rect = renderedRect(rectId);
  hit(rectId);
  fireEvent.pointerDown(surface, pointer([240, 100]));
  fireEvent.pointerMove(surface, pointer([340, 160]));
  expect(renderedRect(rectId)?.x).not.toBe(rect?.x);
  await user.keyboard("{Escape}");
  fireEvent.pointerUp(surface, pointer([340, 160]));
  expect(renderedRect(rectId)).toMatchObject({ x: rect?.x, y: rect?.y });
  expect(undo).toBeDisabled();
  expect(redo).toBeEnabled();

  hit(ellipseId);
  draw(surface, [400, 240], [400, 240]);
  const ellipse = konva.Ellipse.mock.lastCall?.[0];
  hit(ellipseId, true);
  fireEvent.pointerDown(surface, pointer([624, 290], "touch"));
  fireEvent.pointerMove(surface, pointer([700, 330], "touch"));
  expect(konva.Ellipse.mock.lastCall?.[0].radiusX).not.toBe(ellipse?.radiusX);
  fireEvent.lostPointerCapture(surface, pointer([700, 330], "touch"));
  expect(konva.Ellipse.mock.lastCall?.[0]).toMatchObject({
    x: ellipse?.x, y: ellipse?.y, radiusX: ellipse?.radiusX, radiusY: ellipse?.radiusY,
  });
  expect(undo).toBeDisabled();
  await user.click(redo);
  expectCount(5);
});

it("keeps stage coordinates stable through responsive sizing and zoom", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const seeded = renderedRect(rectId);
  await user.click(screen.getByRole("button", { name: "ขยาย" }));
  expect(surface.parentElement).toHaveStyle({ transform: "scale(1.25)" });
  resizeStage(400, 320, new DOMRect(-30, 60, 500, 400));
  expect(surface.querySelector("canvas")).toHaveAttribute("width", "400");
  expect(konva.Group.mock.lastCall?.[0]).toEqual({ scaleX: 4, scaleY: 3.2 });
  expect(renderedRect(rectId)).toMatchObject({ x: seeded?.x, y: seeded?.y, width: seeded?.width });
  // Zoomed stage box includes the letterbox, not just the image’s visible pixels.
  hit(rectId);
  draw(surface, [120, 124], [120, 124]);
  expect(konva.getIntersection).toHaveBeenLastCalledWith({ x: 120, y: 51.2 });
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [20, 100], [70, 140]);
  expect(konva.Arrow.mock.lastCall?.[0].points).toEqual([10, 10, 20, 20]);
});

it("cancels a stationary draw when animated zoom changes the stage bounds", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const undo = screen.getByRole("button", { name: "ย้อนกลับ" });
  const redo = screen.getByRole("button", { name: "ทำซ้ำ" });
  await user.click(screen.getByRole("button", { name: "ขยาย" }));
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [100, 100], [220, 160]);
  await user.click(undo);

  fireEvent.pointerDown(surface, pointer([100, 100]));
  // Same backing buffer; the parent CSS transform changed the visible box.
  resizeStage(800, 500, new DOMRect(-100, -62.5, 1000, 625));
  fireEvent.pointerUp(surface, pointer([100, 100]));
  expectCount(4);
  expect(undo).toBeDisabled();
  expect(redo).toBeEnabled();

  draw(surface, [100, 100], [220, 160]);
  expectCount(5);
});

it("cancels a stationary move when responsive resize changes the stage bounds", async () => {
  renderWorkspace();
  const surface = await getSurface();
  const before = renderedRect(rectId);
  hit(rectId);
  fireEvent.pointerDown(surface, pointer([240, 100], "touch"));
  resizeStage(400, 320, new DOMRect(0, 0, 400, 320));
  fireEvent.pointerUp(surface, pointer([240, 100], "touch"));
  expect(renderedRect(rectId)).toMatchObject({ x: before?.x, y: before?.y });
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
});

it.each([
  ["rectangle", "สี่เหลี่ยม", konva.Rect],
  ["ellipse", "วงรี", konva.Ellipse],
] as const)("moves and resizes a tiny %s using separate targets", async (kind, tool, mark) => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const undo = screen.getByRole("button", { name: "ย้อนกลับ" });
  await user.click(screen.getByRole("button", { name: tool }));
  draw(surface, [100, 100], [106, 106]);
  expectCount(5);

  await user.click(screen.getByRole("button", { name: "เลือก" }));
  const tinyMark = () =>
    mark.mock.calls.filter(([node]) => node.id === "LOCAL-SHAPE-1" && !node.name).at(-1)?.[0];
  const before = tinyMark();
  const handle = konva.Rect.mock.lastCall?.[0];
  expect(handle).toMatchObject({ name: "annotation-resize", id: "LOCAL-SHAPE-1" });
  const centerX = handle!.x! + 5;
  const centerY = handle!.y! + 5;
  // The original mark occupies [100,106] on each axis. The 28px handle hit
  // box must leave that mark clear for its own move hit.
  const separated = centerX + 14 < 100 || centerX - 14 > 106 ||
    centerY + 14 < 100 || centerY - 14 > 106;
  expect(separated).toBe(true);
  hit("LOCAL-SHAPE-1");
  draw(surface, [100, 103], [180, 103]);
  expect(tinyMark()?.x).toBeCloseTo((before?.x ?? 0) + 10);
  await user.click(undo);
  expect(tinyMark()?.x).toBe(before?.x);

  hit("LOCAL-SHAPE-1");
  draw(surface, [100, 103], [100, 103]);
  hit("LOCAL-SHAPE-1", true);
  const grab: [number, number] = [centerX + 3, centerY - 2];
  fireEvent.pointerDown(surface, pointer(grab));
  fireEvent.pointerMove(surface, pointer(grab));
  expect(tinyMark()?.x).toBeCloseTo(before!.x!);
  expect(tinyMark()?.y).toBeCloseTo(before!.y!);
  expect(tinyMark()?.width ?? tinyMark()?.radiusX)
    .toBeCloseTo((before!.width ?? before!.radiusX)!);
  const end: [number, number] = [grab[0] + 32, grab[1] + 20];
  fireEvent.pointerMove(surface, pointer(end));
  fireEvent.pointerUp(surface, pointer(end));
  if (kind === "rectangle") {
    expect(tinyMark()).toMatchObject({
      x: before?.x, y: before?.y, width: expect.closeTo(4.75), height: expect.closeTo(5.2),
    });
  } else {
    expect(tinyMark()).toMatchObject({
      x: expect.closeTo(before!.x! + 2), y: expect.closeTo(before!.y! + 2),
      radiusX: expect.closeTo(2.375), radiusY: expect.closeTo(2.6),
    });
  }
  await user.click(undo);
  expect(tinyMark()?.x).toBe(before?.x);
});

it("keeps the tiny resize target inside the visible stage at a zoomed corner", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const zoom = screen.getByRole("button", { name: "ขยาย" });
  for (let step = 0; step < 4; step++) await user.click(zoom);
  resizeStage(800, 500, new DOMRect(-400, -250, 1600, 1000));
  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  draw(surface, [784, 484], [796, 496]);
  await user.click(screen.getByRole("button", { name: "เลือก" }));
  const handle = konva.Rect.mock.lastCall?.[0];
  expect(handle).toMatchObject({ name: "annotation-resize", id: "LOCAL-SHAPE-1" });
  const centerX = handle!.x! + 5;
  const centerY = handle!.y! + 5;
  // At 2×, only x=200..600 and y=125..375 of the backing stage are visible.
  expect(centerX - 14).toBeGreaterThanOrEqual(200);
  expect(centerX + 14).toBeLessThanOrEqual(600);
  expect(centerY - 14).toBeGreaterThanOrEqual(125);
  expect(centerY + 14).toBeLessThanOrEqual(375);
  expect(centerX + 14 < 592 || centerY + 14 < 367).toBe(true);
});

it("does not jump across the stage edge when resizing a tiny partly clipped mark", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  draw(surface, [100, 100], [106, 106]);
  await user.click(screen.getByRole("button", { name: "เลือก" }));
  hit("LOCAL-SHAPE-1");
  draw(surface, [100, 103], [798, 103]);
  const before = renderedRect("LOCAL-SHAPE-1");
  const handle = konva.Rect.mock.lastCall?.[0];
  expect(handle).toMatchObject({ name: "annotation-resize" });
  hit("LOCAL-SHAPE-1", true);
  const grab: [number, number] = [handle!.x! + 5, handle!.y! + 5];
  const end: [number, number] = [grab[0], grab[1] + 10];
  draw(surface, grab, end);
  expect(renderedRect("LOCAL-SHAPE-1")).toMatchObject({
    x: before?.x, width: expect.closeTo(before!.width!), height: expect.closeTo(3.2),
  });
});

it("Delete cancels an active preview without removing the committed selection", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const surface = await getSurface();
  const undo = screen.getByRole("button", { name: "ย้อนกลับ" });
  hit(rectId);
  draw(surface, [240, 100], [240, 100]);
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  fireEvent.pointerDown(surface, pointer([100, 100]));
  fireEvent.pointerMove(surface, pointer([220, 160]));
  expect(konva.Arrow.mock.lastCall?.[0].id).toBe("LOCAL-SHAPE-1");

  await user.keyboard("{Delete}");
  fireEvent.keyDown(surface, { key: "Delete", repeat: true });
  fireEvent.pointerUp(surface, pointer([220, 160]));
  expectCount(4);
  expect(konva.Arrow.mock.lastCall?.[0].id).toBe("SAMPLE-ANV-3001-2-4");
  expect(undo).toBeDisabled();
  expect(screen.getByRole("button", { name: "ลบที่เลือก" })).toBeEnabled();

  konva.Rect.mockClear();
  await user.keyboard("{Delete}");
  expectCount(3);
  expect(renderedRect(rectId)).toBeUndefined();
  expect(undo).toBeEnabled();
});
