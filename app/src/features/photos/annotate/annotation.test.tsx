import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { server } from "../../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../../test/render";
import { AnnotationWorkspace } from "./AnnotationWorkspace";

function renderWorkspace(patientId = "P-10241") {
  return renderWithFixtures(<AnnotationWorkspace patientId={patientId} />);
}

function draw(svg: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(svg, { clientX: from[0], clientY: from[1] });
  fireEvent.pointerMove(svg, { clientX: to[0], clientY: to[1] });
  fireEvent.pointerUp(svg, { clientX: to[0], clientY: to[1] });
}

it("seeds the authored sample layers and lists versions and drafts", async () => {
  renderWorkspace();
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  // Latest authored version for the before frame has four shapes.
  expect(canvas.querySelectorAll("[data-shape-id]")).toHaveLength(4);
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
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  expect(canvas.querySelectorAll("[data-shape-id]")).toHaveLength(4);

  await user.click(screen.getByRole("button", { name: "ลูกศร" }));
  draw(canvas, [100, 100], [220, 160]);
  expect(canvas.querySelectorAll("[data-shape-id]")).toHaveLength(5);
  expect(screen.getByText(/5 รูปทรง/)).toBeVisible();

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(4);

  await user.click(screen.getByRole("button", { name: "ทำซ้ำ" }));
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(5);

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "ทำซ้ำ" })).toBeEnabled();
});

it("records a freehand stroke as one undoable layer step", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  await user.click(screen.getByRole("button", { name: "วาดอิสระ" }));
  fireEvent.pointerDown(canvas, { clientX: 50, clientY: 50 });
  fireEvent.pointerMove(canvas, { clientX: 80, clientY: 70 });
  fireEvent.pointerMove(canvas, { clientX: 120, clientY: 90 });
  fireEvent.pointerUp(canvas);
  const polylines = canvas.querySelectorAll("polyline");
  expect(polylines).toHaveLength(2); // one authored + the new stroke
  const live = polylines[1];
  expect(live?.getAttribute("points")?.split(" ")).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("polyline"),
  ).toHaveLength(1);
});

it("requires annotation text before placing a label", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  await user.click(screen.getByRole("button", { name: "ข้อความ" }));
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100 });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "กรอกข้อความกำกับก่อน",
  );
  expect(canvas.querySelectorAll("[data-shape-id]")).toHaveLength(4);

  await user.type(screen.getByLabelText("ข้อความกำกับ"), "รอยแดง");
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100 });
  expect(
    screen.getByLabelText("พื้นที่วาดกำกับ").querySelectorAll("text"),
  ).toHaveLength(1);
});

it("deletes the selected shape and can undo the delete", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  const first = canvas.querySelector("[data-shape-id]");
  if (first === null) throw new Error("missing authored shape");
  fireEvent.pointerDown(first, { clientX: 10, clientY: 10 });
  const remove = screen.getByRole("button", { name: "ลบที่เลือก" });
  expect(remove).toBeEnabled();
  await user.click(remove);
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(3);
  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(4);
});

it("saves a client-side version without any transport call", async () => {
  const user = userEvent.setup();
  const writes = vi.fn();
  server.events.on("request:start", (event) => {
    if (event.request.method !== "GET") writes(event.request.url);
  });
  renderWorkspace();
  await screen.findByLabelText("พื้นที่วาดกำกับ");
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
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(2);
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
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  const rect = canvas.querySelector("[data-shape-id]");
  if (rect === null) throw new Error("missing authored shape");
  const beforeX = rect.getAttribute("x");
  const beforeY = rect.getAttribute("y");

  fireEvent.pointerDown(rect, { clientX: 10, clientY: 10 });
  fireEvent.pointerMove(canvas, { clientX: 100, clientY: 80 });
  fireEvent.pointerUp(canvas, { clientX: 100, clientY: 80 });

  const moved = screen
    .getByLabelText("พื้นที่วาดกำกับ")
    .querySelector("[data-shape-id]");
  expect(moved?.getAttribute("x")).not.toBe(beforeX);
  expect(moved?.getAttribute("y")).not.toBe(beforeY);

  await user.click(screen.getByRole("button", { name: "ย้อนกลับ" }));
  const restored = screen
    .getByLabelText("พื้นที่วาดกำกับ")
    .querySelector("[data-shape-id]");
  expect(restored?.getAttribute("x")).toBe(beforeX);
  expect(restored?.getAttribute("y")).toBe(beforeY);
});

it("discards a click that draws no shape", async () => {
  const user = userEvent.setup();
  renderWorkspace();
  const canvas = await screen.findByLabelText("พื้นที่วาดกำกับ");
  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  fireEvent.pointerDown(canvas, { clientX: 50, clientY: 50 });
  fireEvent.pointerUp(canvas, { clientX: 50, clientY: 50 });
  expect(
    screen
      .getByLabelText("พื้นที่วาดกำกับ")
      .querySelectorAll("[data-shape-id]"),
  ).toHaveLength(4);
  expect(screen.getByRole("button", { name: "ย้อนกลับ" })).toBeDisabled();
});
