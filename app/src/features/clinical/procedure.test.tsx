import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import {
  procedureConsumableBatches,
  resolveProcedureConsumables,
} from "../../fixtures/data-clinical";
import { opsData } from "../../fixtures/data-ops";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import {
  NATIVE_BOOTSTRAP,
  nativeSession,
  postSpy,
} from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { ProcedureDetailPage } from "./ProcedureDetailPage";

let restoreDialog: () => void;
beforeAll(() => {
  restoreDialog = installDialogMethods();
});
afterAll(() => {
  restoreDialog();
});

function renderProcedure(id: string, role: "DOC" | "NUR" = "DOC") {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.procedurePattern} element={<ProcedureDetailPage />} />
    </Routes>,
    { role, path: ROUTES.procedure(id) },
  );
}

it("resolves the template consumables to a real usable lot", async () => {
  renderProcedure("DC-PROC-118");
  expect(await screen.findByTestId("procedure-detail")).toBeVisible();
  const resolved = procedureConsumableBatches.find(
    (row) => row.procedureId === "DC-PROC-118",
  );
  const batch = opsData.batchById(resolved?.batchId ?? "");
  expect(batch).not.toBeNull();
  const table = screen.getByRole("table", { name: "เวชภัณฑ์จากแม่แบบ" });
  expect(table).toHaveTextContent(batch?.batchId ?? "missing-lot");
  expect(table).toHaveTextContent(String(batch?.qty));
  // A dense consumables table always scrolls inside the shared wrapper.
  expect(table.parentElement).toHaveClass("pc-tablewrap");
  expect(screen.getByTestId("sample-banner")).toBeVisible();
});

it("previews before → after from the lot quantity, then completes once", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderProcedure("DC-PROC-118");
  const resolved = procedureConsumableBatches.find(
    (row) => row.procedureId === "DC-PROC-118",
  );
  const batch = opsData.batchById(resolved?.batchId ?? "");
  const before = batch?.qty ?? 0;

  await user.click(
    await screen.findByRole("button", { name: "ตรวจสอบผลกระทบสต็อก" }),
  );
  const preview = await screen.findByTestId("procedure-stock-preview");
  expect(preview).toHaveTextContent(`ก่อน ${before} → หลัง ${before - 1}`);

  await user.click(
    screen.getByRole("button", { name: "ยืนยันจบและใช้จริง (ตัวอย่าง)" }),
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));
  await waitFor(() =>
    expect(screen.getAllByText("จบตัวอย่างแล้ว").length).toBeGreaterThan(0),
  );
  expect(
    screen.getByRole("button", { name: "ยืนยันจบและใช้จริง (ตัวอย่าง)" }),
  ).toBeDisabled();
  expect(screen.getAllByText(/ธุรกรรมเซิร์ฟเวอร์ครั้งเดียว/).length).toBeGreaterThan(
    0,
  );
  expect(network.posts).toEqual([]);
  network.stop();
});

it("keeps a scheduled procedure unconfirmable until it starts", async () => {
  renderProcedure("SAMPLE-PROC-NEXT-01");
  expect(await screen.findByText(/ยังไม่เริ่ม จึงยังยืนยันจบไม่ได้/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "ยืนยันจบและใช้จริง (ตัวอย่าง)" }),
  ).toBeDisabled();
});

it("resolves template lots on demand for a procedure without stored rows", () => {
  // The resolver is the only source for scheduled procedures, so verify it
  // maps a two-line template to two usable lots instead of a hard-coded item.
  const rows = resolveProcedureConsumables({
    procedureId: "TEST-PROC",
    encounterId: null,
    templateId: "PRT-02",
  });
  expect(rows.length).toBeGreaterThanOrEqual(1);
  for (const row of rows) expect(opsData.batchById(row.batchId)).not.toBeNull();
});

it("shows the designed not-found state for an unknown procedure", async () => {
  renderProcedure("unknown-1");
  expect(
    await screen.findByText(/ไม่พบหัตถการ|procedure not found/i),
  ).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
});

it.each([390, 834, 1440] as const)(
  "keeps the procedure screen usable at %i",
  async (width) => {
    setViewport(width);
    renderProcedure("DC-PROC-118");
    expect(await screen.findByTestId("procedure-detail")).toBeVisible();
    expect(screen.getByTestId("sample-banner")).toBeVisible();
  },
);

it("renders the procedure detail without network in native mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC")));
  renderWithFixtures(
    <Routes>
      <Route path={ROUTES.procedurePattern} element={<ProcedureDetailPage />} />
    </Routes>,
    { path: ROUTES.procedure("DC-PROC-118"), sessionMode: "live" },
  );
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
  vi.unstubAllEnvs();
});
