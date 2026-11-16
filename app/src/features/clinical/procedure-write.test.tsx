import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { ProcedureDetailPage } from "./ProcedureDetailPage";

const COMPLETE = "/api/method/clinic_ui.api.procedures_write.complete";
const ACK = "/api/method/clinic_ui.api.procedures_write.ack";
const LOCK = "clinic.procedure-write.pending";
const TOKEN = "b".repeat(64);
const WORKSPACE = "WS-PROC-NATIVE-1";

const ENABLED = {
  "write.procedure.complete": "enabled",
  "write.stock.consume": "enabled",
} as const;

function renderNative(role: "DOC" | "NUR" = "DOC") {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.procedurePattern} element={<ProcedureDetailPage />} />
    </Routes>,
    { path: ROUTES.procedure(WORKSPACE), sessionMode: "live" },
  );
}

function serve(caps: Record<string, string>, role: "DOC" | "NUR" = "DOC") {
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession(role, caps)));
}

function success() {
  return HttpResponse.json({
    message: {
      status: "completed",
      procedure: {
        procedure_id: WORKSPACE,
        state: "completed",
        status: "Completed",
      },
      ackToken: TOKEN,
      stockEntryCreated: true,
    },
  });
}

let restoreDialog: () => void;
beforeAll(() => {
  restoreDialog = installDialogMethods();
});
afterAll(() => {
  restoreDialog();
});

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("stays unavailable when the server does not advertise the capability", async () => {
  serve({ "read.patient": "enabled" });
  const network = postSpy();
  renderNative();
  expect(await screen.findByText(/ไม่พบหัตถการ|procedure not found/i)).toBeVisible();
  expect(screen.queryByTestId("procedure-native-complete")).toBeNull();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("renders the native surface and posts nothing until confirmation", async () => {
  serve(ENABLED);
  const network = postSpy();
  renderNative();
  const surface = await screen.findByTestId("procedure-native-complete");
  expect(surface).toBeVisible();
  expect(
    within(surface).getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeEnabled();
  // The stock preview stays read-only in the native surface too.
  expect(screen.getByTestId("procedure-write-stock-note")).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("completes once through one POST and one acknowledgement", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  const writes: string[] = [];
  server.use(
    http.post(COMPLETE, () => {
      writes.push("complete");
      return success();
    }),
    http.post(ACK, () => {
      writes.push("ack");
      return HttpResponse.json({ message: { acknowledged: true } });
    }),
  );
  renderNative();

  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบหัตถการ" }),
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));

  await waitFor(() => expect(writes).toEqual(["complete", "ack"]));
  expect(
    (await screen.findAllByText("จบหัตถการแล้ว")).length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeDisabled();
  expect(localStorage.getItem(LOCK)).toBeNull();
});

it("locks and never retries when the server refuses the completion", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  let attempts = 0;
  server.use(
    http.post(COMPLETE, () => {
      attempts += 1;
      return HttpResponse.json(
        { message: { code: "VALIDATION" } },
        { status: 417 },
      );
    }),
  );
  renderNative();

  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบหัตถการ" }),
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));

  await waitFor(() => expect(attempts).toBe(1));
  expect(
    await screen.findByRole("alert"),
  ).toHaveTextContent(/รายการราคา แบตช์ สต็อก และรายการเวชภัณฑ์/);
  expect(localStorage.getItem(LOCK)).toBe("1");
  expect(
    screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeDisabled();
  expect(attempts).toBe(1);
});

it("keeps a nurse on the read-only note", async () => {
  serve(ENABLED, "NUR");
  renderNative("NUR");
  const surface = await screen.findByTestId("procedure-native-complete");
  expect(
    within(surface).queryByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeNull();
  expect(within(surface).getByText(/เฉพาะแพทย์/)).toBeVisible();
});
