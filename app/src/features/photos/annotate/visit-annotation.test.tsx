import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { useSession } from "../../../app/SessionProvider";
import { renderWithFixtures } from "../../../test/render";
import type { VisitAnnotationCallbacks } from "../../../transport/native/visit-annotations";
import { decodeVisitDrawing, encodeVisitDrawing } from "./visit-drawing";
import { VisitAnnotationWorkspace } from "./VisitAnnotationWorkspace";
import { draw, getSurface, konva, mockKonvaLayout } from "./konva-test-support";

vi.mock("react-konva", () => import("./konva-test-support"));
beforeEach(mockKonvaLayout);

const target = { appointment: "SYNTH-APPT", encounter: "SYNTH-ENC", slot: "Before" } as const;
const image = "data:image/png;base64,iVBORw0KGgo=";
const blank = { version: 0, annotation: null, canWrite: true, pendingRequestKey: null };

async function loadOriginal() {
  const original = await screen.findByRole("img");
  Object.defineProperties(original, { naturalWidth: { value: 800 }, naturalHeight: { value: 500 } });
  fireEvent.load(original);
  expect(original.parentElement).toHaveStyle("--annotation-photo-ratio: 1.6");
  return getSurface();
}

it("round-trips normalized freehand and existing shape geometry", () => {
  const shapes = [
    { id: "F", kind: "freehand", strokeWidth: 0.8, points: [{ x: 0.1, y: 0.2 }, { x: 0.3, y: 0.4 }] },
    { id: "R", kind: "rect", x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
    { id: "E", kind: "ellipse", x: 0.1, y: 0.2, radiusX: 0.15, radiusY: 0.2 },
    { id: "A", kind: "arrow", from: { x: 0.1, y: 0.2 }, to: { x: 0.3, y: 0.4 } },
    { id: "T", kind: "text", x: 0.1, y: 0.2, text: "Sample" },
  ].map((shape) => ({ ...shape, stroke: "#155e75" })) as Parameters<typeof encodeVisitDrawing>[0];
  const wire = encodeVisitDrawing(shapes)!;
  expect(wire).toMatchObject({
    schema_version: 2, coordinate_space: "normalized",
    shapes: [{ type: "freehand", stroke_width: 0.008 }, { type: "rectangle" },
      { type: "ellipse", width: 0.3, height: 0.4 }, { type: "arrow" }, { type: "text" }],
  });
  expect(decodeVisitDrawing(wire)).toEqual(shapes);
});

it("checks the original key, does not claim saved for pending, then reopens only a verified committed version", async () => {
  const user = userEvent.setup();
  const read = vi.fn<VisitAnnotationCallbacks["read"]>().mockResolvedValueOnce(blank);
  const submit = vi.fn<VisitAnnotationCallbacks["submit"]>().mockResolvedValue("pending");
  const status = vi.fn<VisitAnnotationCallbacks["status"]>().mockResolvedValueOnce({ state: "pending" });
  renderWithFixtures(<VisitAnnotationWorkspace target={target} imageUrl={image} callbacks={{ read, submit, status }} />);
  const surface = await loadOriginal();
  expect(screen.queryByTestId("sample-banner")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "วาดอิสระ" }));
  draw(surface, [100, 100], [200, 160]);
  const save = screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" });
  await user.click(save);
  expect(save).toBeDisabled();
  const attempt = submit.mock.calls[0]![0];
  expect(attempt).toMatchObject({ ...target, expected_version: 0, annotation: {
    shapes: [{ type: "freehand", stroke_width: 0.008 }],
  } });
  const check = await screen.findByRole("button", { name: "ตรวจสอบการบันทึก" });
  await user.click(check);
  expect(status.mock.calls[0]![0]).toEqual({ appointment: target.appointment, slot: target.slot, request_key: attempt.request_key });
  expect(screen.queryByText("บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน")).not.toBeInTheDocument();
  status.mockResolvedValueOnce({ state: "committed", version: 1, request_key: "different-key" });
  await user.click(check);
  expect(await screen.findByText("ยังยืนยันการบันทึกไม่ได้ โปรดตรวจสอบก่อนลองอีกครั้ง")).toBeVisible();
  expect(read).toHaveBeenCalledTimes(1);
  status.mockResolvedValueOnce({ state: "committed", version: 1, request_key: attempt.request_key });
  read.mockResolvedValueOnce({ ...blank, version: 1, annotation: attempt.annotation });
  await user.click(check);
  expect(await screen.findByText("บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน")).toBeVisible();
  expect(read.mock.calls[1]![0]).toEqual({ appointment: target.appointment, slot: target.slot, version: 1 });
  expect(submit).toHaveBeenCalledTimes(1);
  const reopenedSurface = await loadOriginal();
  await user.click(screen.getByRole("button", { name: "วาดอิสระ" }));
  draw(reopenedSurface, [100, 100], [160, 160]);
  await waitFor(() => expect(konva.Line.mock.lastCall?.[0].id).not.toBe(attempt.annotation.shapes[0]!.id));
});

it("samples long strokes to native point and byte caps without losing marks or endpoints", () => {
  const points = Array.from({ length: 1300 }, (_, i) => ({ x: i / 1299, y: Math.sin(i) ** 2 }));
  const encoded = encodeVisitDrawing(Array.from({ length: 16 }, (_, i) => ({
    id: `S${i}`, kind: "freehand", stroke: "#155e75", strokeWidth: 0.8, points,
  })))!;
  expect(encoded.shapes).toHaveLength(16);
  let total = 0;
  for (const shape of encoded.shapes) {
    if (shape.type !== "freehand") throw new Error("freehand mark lost");
    expect(shape.points[0]).toEqual(points[0]);
    expect(shape.points.at(-1)).toEqual(points.at(-1));
    expect(shape.points.length).toBeLessThanOrEqual(1024);
    total += shape.points.length;
  }
  expect(total).toBeLessThanOrEqual(8192);
  expect(new TextEncoder().encode(JSON.stringify(encoded)).byteLength).toBeLessThanOrEqual(256 * 1024);
});

it("keeps stale edits without another save until latest version is explicitly loaded", async () => {
  const user = userEvent.setup();
  const read = vi.fn<VisitAnnotationCallbacks["read"]>().mockResolvedValue(blank);
  const submit = vi.fn<VisitAnnotationCallbacks["submit"]>().mockResolvedValue("stale");
  const status = vi.fn<VisitAnnotationCallbacks["status"]>();
  renderWithFixtures(<VisitAnnotationWorkspace target={target} imageUrl={image} callbacks={{ read, submit, status }} />);
  const surface = await loadOriginal();
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [100, 100], [200, 160]);
  await user.click(screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" }));
  expect(await screen.findByText("มีเวอร์ชันใหม่กว่า การแก้ไขของคุณยังไม่ได้บันทึก")).toBeVisible();
  expect(screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" })).toBeDisabled();
  expect(read).toHaveBeenCalledTimes(1);
  expect(status).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "ทิ้งการแก้ไขและเปิดเวอร์ชันล่าสุด" }));
  expect(read).toHaveBeenCalledTimes(2);
  expect(submit).toHaveBeenCalledTimes(1);
});

it.each([true, false])("refreshes after a known rejection without replacing edits (existing intent: %s)", async (existing) => {
  const user = userEvent.setup();
  const read = vi.fn<VisitAnnotationCallbacks["read"]>()
    .mockResolvedValueOnce(blank)
    .mockResolvedValueOnce({ ...blank, pendingRequestKey: existing ? "ORIGINAL-REQUEST" : null });
  const submit = vi.fn<VisitAnnotationCallbacks["submit"]>().mockResolvedValue("rejected");
  const status = vi.fn<VisitAnnotationCallbacks["status"]>().mockResolvedValue({ state: "pending" });
  renderWithFixtures(<VisitAnnotationWorkspace target={target} imageUrl={image} callbacks={{ read, submit, status }} />);
  const surface = await loadOriginal();
  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(surface, [100, 100], [200, 160]);
  const mark = konva.Arrow.mock.lastCall?.[0];
  await user.click(screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(read.mock.calls[1]![0]).toEqual({ appointment: target.appointment, slot: target.slot });
  expect(await getSurface()).toBe(surface);
  expect(konva.Arrow.mock.lastCall?.[0]).toMatchObject({ id: mark?.id, points: mark?.points });
  expect(status).not.toHaveBeenCalled();
  if (existing) {
    const check = await screen.findByRole("button", { name: "ตรวจสอบการบันทึก" });
    await user.click(check);
    expect(status.mock.calls[0]![0]).toEqual({
      appointment: target.appointment, slot: target.slot, request_key: "ORIGINAL-REQUEST",
    });
    expect(submit.mock.calls[0]![0].request_key).not.toBe("ORIGINAL-REQUEST");
    status.mockResolvedValueOnce({ state: "committed", request_key: "ORIGINAL-REQUEST", version: 1 });
    read.mockResolvedValueOnce({ ...blank, version: 1, annotation: encodeVisitDrawing([]) });
    await user.click(check);
    expect(await screen.findByText("มีเวอร์ชันใหม่กว่า การแก้ไขของคุณยังไม่ได้บันทึก")).toBeVisible();
    expect(await getSurface()).toBe(surface);
    expect(konva.Arrow.mock.lastCall?.[0]).toMatchObject({ id: mark?.id, points: mark?.points });
  } else {
    expect(await screen.findByText("ไม่ได้บันทึกภาพวาดกำกับ")).toBeVisible();
    expect(screen.queryByRole("button", { name: "ตรวจสอบการบันทึก" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeEnabled();
  }
  expect(submit).toHaveBeenCalledTimes(1);
});

it("recovers server intent, aborts status on slot or auth change, and never applies a late commit", async () => {
  const user = userEvent.setup();
  const read = vi.fn<VisitAnnotationCallbacks["read"]>().mockImplementation(async ({ slot }) =>
    slot === "Before" ? { ...blank, pendingRequestKey: "SYNTH-key" } : { ...blank, canWrite: false });
  const submit = vi.fn<VisitAnnotationCallbacks["submit"]>();
  let resolve!: (status: Awaited<ReturnType<VisitAnnotationCallbacks["status"]>>) => void;
  const status = vi.fn<VisitAnnotationCallbacks["status"]>().mockImplementation(
    () => new Promise((done) => { resolve = done; }),
  );
  function TestSlots() {
    const [slot, setSlot] = useState<"Before" | "After">("Before");
    const { logout } = useSession();
    return <>
      <button onClick={() => setSlot(slot === "Before" ? "After" : "Before")}>Switch slot</button>
      <button onClick={() => void logout()}>End session</button>
      <VisitAnnotationWorkspace target={{ ...target, slot }} imageUrl={image} callbacks={{ read, submit, status }} />
    </>;
  }
  renderWithFixtures(<TestSlots />);
  await user.click(await screen.findByRole("button", { name: "ตรวจสอบการบันทึก" }));
  expect(status.mock.calls[0]![0]).toEqual({ appointment: target.appointment, slot: "Before", request_key: "SYNTH-key" });
  await user.click(screen.getByRole("button", { name: "Switch slot" }));
  expect(status.mock.calls[0]![1].aborted).toBe(true);
  await act(async () => resolve({ state: "committed", version: 1, request_key: "SYNTH-key" }));
  expect(await screen.findByText("ภาพวาดกำกับนี้ดูได้อย่างเดียว")).toBeVisible();
  expect(read.mock.calls.every(([input]) => input.version === undefined)).toBe(true);
  expect(screen.queryByText("บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Switch slot" }));
  await user.click(await screen.findByRole("button", { name: "ตรวจสอบการบันทึก" }));
  await user.click(screen.getByRole("button", { name: "End session" }));
  await waitFor(() => expect(status.mock.calls[1]![1].aborted).toBe(true));
  await act(async () => resolve({ state: "committed", version: 1, request_key: "SYNTH-key" }));
  expect(read.mock.calls.every(([input]) => input.version === undefined)).toBe(true);
  expect(submit).not.toHaveBeenCalled();
});
