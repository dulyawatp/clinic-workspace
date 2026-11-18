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
const CREATE = "/api/method/clinic_ui.api.procedures_write.create";
const VIEW = "/api/method/clinic_ui.api.procedures_write.view";
const LOOKUP = "/api/method/clinic_ui.api.procedures_write.by_service_request";
const ACK = "/api/method/clinic_ui.api.procedures_write.ack";
const WORKSPACE = "WS-PROC-NATIVE-1";
const NATIVE_DOCNAME = "PROC-0001";

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
      stockEntryCreated: true,
    },
  });
}

/** Fails the test if any code path still posts the retired acknowledgement. */
function guardAck(writes: string[]) {
  server.use(
    http.post(ACK, () => {
      writes.push("ack");
      return HttpResponse.json({ message: { acknowledged: true } });
    }),
  );
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
  server.use(http.get(VIEW, ({ request }) => HttpResponse.json({ message: {
    procedure: { procedure_id: new URL(request.url).searchParams.get("procedure_id"), status: "Pending", docstatus: 0 },
  } })));
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("shows read-only status when the server does not advertise write capability", async () => {
  serve({ "read.patient": "enabled" });
  const network = postSpy();
  renderNative();
  expect(await screen.findByText(/สถานะ: Pending/)).toBeVisible();
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

it("completes once through one POST and no acknowledgement", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  const writes: string[] = [];
  guardAck(writes);
  server.use(
    http.post(COMPLETE, () => {
      writes.push("complete");
      return success();
    }),
  );
  renderNative();

  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบหัตถการ" }),
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));

  await waitFor(() => expect(writes).toEqual(["complete"]));
  expect(
    (await screen.findAllByText("จบหัตถการแล้ว")).length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeDisabled();
  // No persisted browser fence is written or cleared by a converted write.
  expect(localStorage.length).toBe(0);
});

it("keeps one in-flight completion request while the first is pending", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  const writes: string[] = [];
  let release: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  guardAck(writes);
  server.use(
    http.post(COMPLETE, () => {
      writes.push("complete");
      return pending;
    }),
  );
  renderNative();

  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบหัตถการ" }),
  );
  const dialog = await screen.findByRole("dialog");
  const confirm = within(dialog).getByRole("button", { name: "ยืนยัน" });
  await user.click(confirm);
  await user.click(confirm);

  expect(writes).toEqual(["complete"]);

  release(
    new Response(
      JSON.stringify({
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "Completed",
          },
          stockEntryCreated: true,
        },
      }),
      { status: 200 },
    ),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
    ).toBeDisabled(),
  );
  expect(writes).toEqual(["complete"]);
});

it("reports a definite refusal once and allows an explicit retry", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  let attempts = 0;
  guardAck([]);
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
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /รายการราคา แบตช์ สต็อก และรายการเวชภัณฑ์/,
  );
  // A pre-mutation refusal is a known outcome: nothing is locked, no retry ran.
  expect(
    screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeEnabled();
  expect(attempts).toBe(1);
  expect(localStorage.length).toBe(0);
});

it("stops the screen after an uncertain completion and never retries", async () => {
  const user = userEvent.setup();
  serve(ENABLED);
  let attempts = 0;
  const writes: string[] = [];
  guardAck(writes);
  server.use(
    http.post(COMPLETE, () => {
      attempts += 1;
      return HttpResponse.json(
        { message: { code: "WRITE_UNCERTAIN" } },
        { status: 409 },
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
  expect(await screen.findByRole("alert")).toHaveTextContent(/ผลลัพธ์ไม่แน่นอน/);
  expect(
    screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }),
  ).toBeDisabled();
  expect(attempts).toBe(1);
  expect(writes).toEqual([]);
  expect(localStorage.length).toBe(0);
});

it("creates one procedure from a submitted service request and shows the native docname", async () => {
  const user = userEvent.setup();
  serve({ "write.procedure.create": "enabled" });
  const writes: string[] = [];
  const bodies: Record<string, unknown>[] = [];
  guardAck(writes);
  server.use(
    http.post(CREATE, async ({ request }) => {
      writes.push("create");
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "created",
          procedure: {
            procedure_id: NATIVE_DOCNAME,
            state: "pending",
            status: "Pending",
          },
        },
      });
    }),
  );
  renderNative();

  await user.type(
    await screen.findByLabelText("ใบขอรับบริการ"),
    "SR-SIT-0001",
  );
  await user.click(screen.getByRole("button", { name: "สร้างหัตถการ" }));

  await waitFor(() => expect(writes).toEqual(["create"]));
  expect(bodies[0]).toEqual({
    operation_key: expect.any(String),
    service_request: "SR-SIT-0001",
  });
  expect(
    (bodies[0]!.operation_key as string).length,
  ).toBeGreaterThanOrEqual(16);
  expect(await screen.findByTestId("procedure-created")).toHaveTextContent(
    NATIVE_DOCNAME,
  );
  expect(within(screen.getByTestId("procedure-created")).getByRole("link")).toHaveAttribute("href", ROUTES.procedure(NATIVE_DOCNAME));
  expect(localStorage.length).toBe(0);
});

it("reports a definite creation refusal once and leaves the form usable", async () => {
  const user = userEvent.setup();
  serve({ "write.procedure.create": "enabled" });
  let attempts = 0;
  guardAck([]);
  server.use(
    http.post(CREATE, () => {
      attempts += 1;
      return HttpResponse.json(
        { message: { code: "VALIDATION" } },
        { status: 417 },
      );
    }),
  );
  renderNative();

  await user.type(
    await screen.findByLabelText("ใบขอรับบริการ"),
    "SR-SIT-0002",
  );
  const submit = screen.getByRole("button", { name: "สร้างหัตถการ" });
  await user.click(submit);

  await waitFor(() => expect(attempts).toBe(1));
  expect(await screen.findByRole("alert")).toHaveTextContent(/ใบขอรับบริการ/);
  expect(submit).toBeEnabled();

  // An explicit second attempt is allowed: the first refusal changed nothing.
  await user.click(submit);
  await waitFor(() => expect(attempts).toBe(2));
  expect(localStorage.length).toBe(0);
});

it("stops creation after an uncertain outcome and never retries", async () => {
  const user = userEvent.setup();
  serve({ "write.procedure.create": "enabled" });
  let attempts = 0;
  guardAck([]);
  server.use(
    http.get(LOOKUP, ({ request }) => {
      expect(new URL(request.url).searchParams.get("service_request")).toBe("SR-SIT-0003");
      return HttpResponse.json({ message: { items: [{ procedure_id: "PROC-FOUND", status: "Pending", docstatus: 0 }] } });
    }),
    http.post(CREATE, () => {
      attempts += 1;
      return HttpResponse.json(
        { message: { code: "WRITE_UNCERTAIN" } },
        { status: 409 },
      );
    }),
  );
  renderNative();

  await user.type(
    await screen.findByLabelText("ใบขอรับบริการ"),
    "SR-SIT-0003",
  );
  await user.click(screen.getByRole("button", { name: "สร้างหัตถการ" }));

  await waitFor(() => expect(attempts).toBe(1));
  expect(await screen.findByRole("alert")).toHaveTextContent(/ผลลัพธ์ไม่แน่นอน/);
  expect(screen.getByRole("button", { name: "สร้างหัตถการ" })).toBeDisabled();
  expect(screen.getByLabelText("ใบขอรับบริการ")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("ใบขอรับบริการ")).toHaveAccessibleDescription(/ตรวจสอบข้อมูล/);
  await user.click(screen.getByRole("button", { name: "ค้นหาหัตถการสำหรับใบขอรับบริการนี้" }));
  expect(await screen.findByRole("link", { name: "PROC-FOUND" })).toHaveAttribute("href", ROUTES.procedure("PROC-FOUND"));
  expect(attempts).toBe(1);
  expect(localStorage.length).toBe(0);
});

it.each([true, false])("re-reads status after completion (read succeeds: %s)", async (readSucceeds) => {
  const user = userEvent.setup();
  serve(ENABLED);
  let reads = 0;
  server.use(
    http.get(VIEW, () => {
      reads += 1;
      if (reads > 1 && !readSucceeds) return HttpResponse.json({}, { status: 503 });
      return HttpResponse.json({ message: { procedure: {
        procedure_id: WORKSPACE, status: reads === 1 ? "Pending" : "Completed", docstatus: reads === 1 ? 0 : 1,
      } } });
    }),
    http.post(COMPLETE, () => success()),
  );
  renderNative();
  expect(await screen.findByText(/สถานะ: Pending/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "ยืนยันจบหัตถการ" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "ยืนยัน" }));
  if (readSucceeds) {
    expect(await screen.findByText(/สถานะ: Completed/)).toBeVisible();
  } else {
    expect(await screen.findByRole("alert")).toHaveTextContent("ไม่สามารถดูข้อมูลได้");
  }
  expect(screen.queryByText(/สถานะ: Pending/)).toBeNull();
  expect(reads).toBe(2);
});

it("shows permission and unavailable errors rather than stale status", async () => {
  const user = userEvent.setup();
  serve({}, "NUR");
  let calls = 0;
  server.use(http.get(VIEW, () => {
    calls += 1;
    if (calls === 1) return HttpResponse.json({ message: { procedure: { procedure_id: WORKSPACE, status: "Pending", docstatus: 0 } } });
    return HttpResponse.json({}, { status: calls === 2 ? 403 : 503 });
  }));
  renderNative("NUR");
  expect(await screen.findByText(/สถานะ: Pending/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "โหลดสถานะใหม่" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("คุณไม่มีสิทธิ์");
  expect(screen.queryByText(/สถานะ: Pending/)).toBeNull();
  await user.click(screen.getByRole("button", { name: "โหลดสถานะใหม่" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ไม่สามารถดูข้อมูลได้");
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
