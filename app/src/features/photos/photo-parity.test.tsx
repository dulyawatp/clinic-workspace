import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { draw, expectCount, getSurface, mockKonvaLayout } from "./annotate/konva-test-support";
import { PhotoComparePage, PhotoGalleryPage } from "./PhotoPages";

vi.mock("react-konva", () => import("./annotate/konva-test-support"));

const PATH = "/clinic/patients/PAT-1/photos";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

function renderGallery() {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/patients/:id/photos" element={<PhotoGalleryPage />} />
    </Routes>,
    { role: "DOC", path: PATH },
  );
}

function postSpy() {
  const posts = vi.fn();
  const listener = (event: { request: Request }) => {
    if (event.request.method !== "GET") posts(event.request.url);
  };
  server.events.on("request:start", listener);
  return {
    posts,
    stop: () => server.events.removeListener("request:start", listener),
  };
}

it("renders the designed intake flow with closed capture controls", async () => {
  renderGallery();
  await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/);
  const intake = screen.getByRole("region", { name: "ขั้นตอนรับภาพถ่าย" });
  expect(within(intake).getByText("1 · ความยินยอม")).toBeVisible();
  expect(within(intake).getByText("2 · ต้นฉบับ")).toBeVisible();
  expect(within(intake).getByText("3 · แท็กและเชื่อม")).toBeVisible();
  expect(within(intake).getByText("มีต้นฉบับ 2 ภาพ")).toBeVisible();
  expect(
    within(intake).getByText("ยังไม่มีหลักฐานความยินยอม"),
  ).toBeVisible();
  expect(
    within(intake).getByRole("button", { name: "เริ่มรับภาพ" }),
  ).toBeDisabled();
  expect(
    within(intake).getByRole("button", { name: "เลือกไฟล์ต้นฉบับ" }),
  ).toBeDisabled();
});

it("offers consent capture as a designed control only", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderGallery();
  await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/);
  await user.click(
    screen.getByRole("button", { name: "บันทึกหลักฐานความยินยอม" }),
  );
  const dialog = await screen.findByRole("region", {
    name: "หลักฐานความยินยอมถ่ายภาพ",
  });
  expect(
    within(dialog).getAllByText(/การบันทึกความยินยอมยังไม่เปิดใช้งานในสัญญานี้/)[0],
  ).toBeVisible();
  expect(within(dialog).getByLabelText("ชื่อผู้ลงนาม")).toBeVisible();
  expect(within(dialog).getByLabelText("วันที่ลงนาม")).toBeVisible();
  expect(
    within(dialog).getByLabelText("เลขอ้างอิงเอกสารความยินยอม"),
  ).toBeVisible();
  const submit = within(dialog).getAllByRole("button", {
    name: "บันทึกหลักฐานความยินยอม",
  })[0];
  expect(submit).toBeDisabled();
  expect(network.posts).not.toHaveBeenCalled();
  network.stop();
});

it("edits body-area tags locally and never writes them to a server", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderGallery();
  await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/);
  const tiles = screen.getAllByRole("figure");
  const first = tiles[0];
  if (first === undefined) throw new Error("missing photo tile");
  await user.click(within(first).getByRole("button", { name: "แก้ไขแท็ก" }));
  await user.selectOptions(
    within(first).getByLabelText("บริเวณ"),
    "scalp",
  );
  await user.selectOptions(within(first).getByLabelText("มุมภาพ"), "left");
  await user.click(
    within(first).getByRole("button", { name: "บันทึกแท็กตัวอย่าง" }),
  );
  await waitFor(() =>
    expect(within(first).getByText(/บริเวณร่างกาย: หนังศีรษะ/)).toBeVisible(),
  );
  expect(within(first).getByText(/มุมภาพ: ด้านซ้าย/)).toBeVisible();
  expect(
    await screen.findByText(/แท็กตัวอย่างในเครื่อง ยังไม่บันทึกไปเซิร์ฟเวอร์/),
  ).toBeVisible();
  expect(network.posts).not.toHaveBeenCalled();
  network.stop();
});

it("keeps the fixture annotation workspace network-free in native mode", async () => {
  mockKonvaLayout();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const network = postSpy();
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/patients/:id/photos/compare" element={<PhotoComparePage />} />
    </Routes>,
    { role: "DOC", path: ROUTES.photoCompare("P-10241"), sessionMode: "live" },
  );
  // The proxy photo read is unavailable in the native build, so the fixture
  // annotation block renders on its own without inventing a photo.
  expect(
    await screen.findByTestId("sample-banner"),
  ).toBeVisible();
  expect(
    screen.getByLabelText("พื้นที่วาดกำกับ"),
  ).toBeInTheDocument();
  expect(network.posts).not.toHaveBeenCalled();
  network.stop();
});

it("annotates the compared originals in place, with no extra selector", async () => {
  mockKonvaLayout();
  const user = userEvent.setup();
  const network = postSpy();
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/patients/:id/photos/compare" element={<PhotoComparePage />} />
    </Routes>,
    { role: "DOC", path: ROUTES.photoCompare("PAT-1") },
  );
  await screen.findByTestId("compare-pair");
  const surface = await getSurface();
  // The proxy compare already owns the before/after selectors; the annotation
  // workspace draws on the displayed original instead of adding a third.
  expect(screen.getAllByRole("combobox")).toHaveLength(2);
  expectCount(0);

  await user.click(screen.getByRole("button", { name: "สี่เหลี่ยม" }));
  draw(surface, [40, 40], [160, 120]);
  expectCount(1);

  await user.click(
    screen.getByRole("button", { name: "บันทึกเวอร์ชันคำอธิบาย" }),
  );
  expect(
    await screen.findByText("บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน"),
  ).toBeVisible();
  expect(network.posts).not.toHaveBeenCalled();
  network.stop();
});
