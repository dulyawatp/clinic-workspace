import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import type { CapabilityMap } from "../../domain/capability";
import { coreData } from "../../fixtures/data";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AuthoredVisitWorkspace } from "./AuthoredVisitWorkspace";
import { VisitWorkspacePage } from "./VisitWorkspacePage";

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  localStorage.clear();
});

let restoreDialog: () => void;
beforeAll(() => {
  restoreDialog = installDialogMethods();
});
afterAll(() => {
  restoreDialog();
});

function renderAuthored() {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.visitPattern} element={<VisitWorkspacePage />} />
    </Routes>,
    { role: "DOC", path: ROUTES.visit("DC-VIS-2041") },
  );
}

it("opens the authored sample encounter with every designed section", async () => {
  renderAuthored();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent(
    "DC-VIS-2041",
  );
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(10);
  for (const label of [
    "ภาพรวม",
    "ประวัติ",
    "การตรวจร่างกาย",
    "บันทึก",
    "การวินิจฉัย",
    "ยา",
    "หัตถการ",
    "ผลแล็บ",
    "ภาพถ่าย",
    "สรุป",
  ])
    expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeVisible();
  // The three former placeholders are real designed sections now.
  expect(
    screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
  ).toBeNull();
});

it("guides readiness from the canonical fields", async () => {
  const user = userEvent.setup();
  renderAuthored();
  const readiness = await screen.findByRole("status");
  expect(readiness).toHaveTextContent("ครบแล้ว ตรวจทานก่อนยืนยัน");

  await user.click(screen.getByRole("tab", { name: /ประวัติ|history/i }));
  const complaint = await screen.findByLabelText("อาการสำคัญ");
  await user.clear(complaint);
  expect(screen.getByRole("status")).toHaveTextContent("ยังต้องกรอก");
  expect(screen.getByRole("status")).toHaveTextContent("อาการสำคัญ");
});

it("shows the patient's external lab request and the procedure link", async () => {
  const user = userEvent.setup();
  renderAuthored();
  await user.click(await screen.findByRole("tab", { name: /ผลแล็บ|lab/i }));
  expect(await screen.findByRole("link", { name: "เปิดคำขอ" })).toHaveAttribute(
    "href",
    ROUTES.lab("DC-LAB-77"),
  );
  expect(screen.getByRole("heading", { name: "ผลตรวจในคลินิก" })).toBeVisible();

  await user.click(screen.getByRole("tab", { name: /หัตถการ|procedures/i }));
  expect(
    await screen.findByRole("link", { name: "เตรียมและยืนยันใช้จริง" }),
  ).toHaveAttribute("href", ROUTES.procedure("DC-PROC-118"));
});

it("keeps the new lab request control explicitly unavailable", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(await screen.findByRole("tab", { name: /ผลแล็บ|lab/i }));
  const control = await screen.findByRole("button", { name: "สั่งตรวจใหม่" });
  expect(control).toBeDisabled();
  expect(control).toHaveAccessibleDescription(
    "การสั่งตรวจใหม่ไม่มีในสัญญานี้ ระบบจะไม่ส่งคำขอ",
  );
  await user.click(control);
  expect(network.posts).toEqual([]);
  network.stop();
});

it("keeps a local draft inside the screen and sends nothing", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(await screen.findByRole("button", { name: "บันทึกฉบับร่าง" }));
  const status = screen.getByTestId("visit-save-status");
  expect(status).toHaveTextContent("ไม่ได้ส่งไปเซิร์ฟเวอร์");
  expect(network.posts).toEqual([]);
  network.stop();
});

it("locks the sample encounter once completed and keeps an amendment apart", async () => {
  const user = userEvent.setup();
  const network = postSpy();
  renderAuthored();
  await user.click(
    await screen.findByRole("button", { name: "ยืนยันจบการตรวจตัวอย่าง" }),
  );
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("ไม่ใช่การลงนามแพทย์จริง");
  await user.click(within(dialog).getByRole("button", { name: "ยืนยัน" }));

  await waitFor(() =>
    expect(screen.getByTestId("visit-save-status")).toHaveTextContent(
      "เสร็จสิ้นตัวอย่าง",
    ),
  );
  expect(screen.getByRole("button", { name: "บันทึกฉบับร่าง" })).toBeDisabled();

  // Navigate to the review section: the amendment control appears only here.
  await user.click(screen.getByRole("tab", { name: /สรุป|review/i }));
  await user.click(
    await screen.findByRole("button", { name: "แก้ไขหลังลงนาม (คำขอใหม่)" }),
  );
  const amend = await screen.findByRole("dialog");
  const confirm = within(amend).getByRole("button", { name: "ยืนยัน" });
  expect(confirm).toBeDisabled();
  await user.type(
    within(amend).getByLabelText("เหตุผลและข้อความเพิ่มเติม"),
    "เพิ่มความรุนแรงของสิวตามภาพถ่าย",
  );
  await user.click(confirm);
  expect(await screen.findByTestId("visit-addendum")).toHaveTextContent(
    "ภาคผนวกแยก",
  );
  expect(screen.getByText("เก็บคำขอแก้ไขแยก ไม่ทับต้นฉบับ")).toBeVisible();
  expect(network.posts).toEqual([]);
  network.stop();
});

it("keeps the desktop rail at the tablet breakpoint", async () => {
  setViewport(834);
  renderAuthored();
  expect(await screen.findByRole("tablist")).toBeVisible();
  expect(screen.queryByTestId("section-accordion")).toBeNull();
});

it("stacks the authored editor as one-open accordions at 390", async () => {
  setViewport(390);
  renderAuthored();
  expect(await screen.findByTestId("section-accordion")).toBeVisible();
  expect(screen.getByTestId("sample-banner")).toBeVisible();
  expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(1);
});

it("renders the authored editor without a proxy read", async () => {
  const network = postSpy();
  const reads: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("/api/read/")) reads.push(event.request.url);
  });
  const visit = coreData.visitById("DC-VIS-2041");
  if (!visit) throw new Error("fixture encounter missing");
  renderWithFixtures(<AuthoredVisitWorkspace visit={visit} />, {
    role: "DOC",
  });
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  expect(reads).toEqual([]);
  expect(network.posts).toEqual([]);
  network.stop();
  server.events.removeAllListeners("request:start");
});

// -- native encounter write loop -----------------------------------------

const NATIVE_ENCOUNTER = `wse1_${"a".repeat(32)}_${"b".repeat(16)}_native-draft-key-0001`;
const NATIVE_APPOINTMENT = `wsa1_${"c".repeat(32)}_${"d".repeat(16)}_appt-book-key-0001-012`;
const ACK_TOKEN = "a".repeat(64);
const SAVE_DRAFT =
  "/api/method/clinic_ui.api.encounters_write.save_draft";
const ACK = "/api/method/clinic_ui.api.encounters_write.ack";

function nativeCaps(extra: Record<string, string> = {}) {
  return {
    "write.encounter.draft": "enabled",
    "write.encounter.complete": "enabled",
    "write.encounter.amend": "enabled",
    ...extra,
  };
}

function nativeWorkspace() {
  return (
    <AuthoredVisitWorkspace
      native={{ patientId: "PT-1", appointmentId: NATIVE_APPOINTMENT }}
    />
  );
}

function serveEncounterWrite(
  outcome: "ok" | "fail" = "ok",
  caps: Record<string, string> = nativeCaps(),
) {
  return [
    http.get(NATIVE_BOOTSTRAP, () =>
      nativeSession("DOC", caps as CapabilityMap),
    ),
    http.post(SAVE_DRAFT, () =>
      outcome === "ok"
        ? HttpResponse.json({
            message: {
              status: "draft_saved",
              ackToken: ACK_TOKEN,
              encounter: {
                encounter_id: NATIVE_ENCOUNTER,
                workspace_patient_id: "PT-1",
                state: "draft",
                status: "Draft",
                modified: "2026-11-14 09:30:00.123456",
              },
            },
          })
        : HttpResponse.json({ message: { code: "VALIDATION" } }, { status: 422 }),
    ),
    http.post(ACK, () => HttpResponse.json({ message: { acknowledged: true } })),
  ];
}

it("shows the native write surface, not the demo banner", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(...serveEncounterWrite());
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "DOC",
    caps: nativeCaps() as CapabilityMap,
    path: ROUTES.visit("ENC-1"),
  });
  expect(await screen.findByTestId("native-visit-workspace")).toBeVisible();
  expect(screen.getByTestId("native-write-banner")).toBeVisible();
  expect(screen.queryByTestId("sample-banner")).toBeNull();
});

it("saves the native draft and acknowledges exactly once for DOC", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  const posts = postSpy();
  server.use(...serveEncounterWrite());
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "DOC",
    caps: nativeCaps() as CapabilityMap,
    path: ROUTES.visit("ENC-1"),
  });
  await user.click(await screen.findByRole("button", { name: "บันทึกฉบับร่าง" }));
  await waitFor(() =>
    expect(posts.posts.some((url) => url.includes("save_draft"))).toBe(true),
  );
  await waitFor(() =>
    expect(posts.posts.some((url) => url.includes("encounters_write.ack"))).toBe(
      true,
    ),
  );
  posts.stop();
});

it("never writes when the capabilities are not enabled for the role", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  const posts = postSpy();
  server.use(...serveEncounterWrite("ok", {}));
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "NUR",
    caps: {},
    path: ROUTES.visit("ENC-1"),
  });
  const save = await screen.findByRole("button", { name: "บันทึกฉบับร่าง" });
  expect(save).toBeDisabled();
  const complete = screen.getByRole("button", {
    name: "ยืนยันจบการตรวจตัวอย่าง",
  });
  expect(complete).toBeDisabled();
  await user.click(save);
  await user.click(complete);
  expect(posts.posts).toEqual([]);
  posts.stop();
});

it("reports a refused native save without retrying", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  const posts = postSpy();
  server.use(...serveEncounterWrite("fail"));
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "DOC",
    caps: nativeCaps() as CapabilityMap,
    path: ROUTES.visit("ENC-1"),
  });
  await user.click(await screen.findByRole("button", { name: "บันทึกฉบับร่าง" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "มีบางฟิลด์ขาดหรือไม่ถูกต้อง",
  );
  expect(posts.posts.filter((url) => url.includes("save_draft"))).toHaveLength(1);
  posts.stop();
});

it("authors medication and lab rows into the native draft payload", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  let body: Record<string, unknown> | null = null;
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      nativeSession("DOC", nativeCaps() as CapabilityMap),
    ),
    http.post(SAVE_DRAFT, async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        message: {
          status: "draft_saved",
          ackToken: ACK_TOKEN,
          encounter: {
            encounter_id: NATIVE_ENCOUNTER,
            workspace_patient_id: "PT-1",
            state: "draft",
            status: "Draft",
            modified: "2026-11-14 09:30:00.123456",
          },
        },
      });
    }),
    http.post(ACK, () => HttpResponse.json({ message: { acknowledged: true } })),
  );
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "DOC",
    caps: nativeCaps() as CapabilityMap,
    path: ROUTES.visit("ENC-1"),
  });

  await user.click(await screen.findByRole("tab", { name: /ยา|medication/i }));
  await user.click(
    await screen.findByRole("button", { name: "เพิ่มยา" }),
  );
  await user.type(
    screen.getByLabelText("รหัสยา"),
    "DRUG-1",
  );
  await user.type(screen.getByLabelText("ขนาดยา"), "1 tab");

  await user.click(screen.getByRole("tab", { name: /ผลแล็บ|lab/i }));
  await user.click(
    await screen.findByRole("button", { name: "สั่งตรวจแล็บ" }),
  );
  await user.type(screen.getByLabelText("รหัสตรวจแล็บ"), "CBC");

  await user.click(screen.getByRole("button", { name: "บันทึกฉบับร่าง" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body).toMatchObject({
    medications: [{ drug_code: "DRUG-1", dosage: "1 tab" }],
    lab_tests: [{ lab_test_code: "CBC" }],
    procedures: [],
  });
});

it("removes an authored row before it reaches the payload", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  let body: Record<string, unknown> | null = null;
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      nativeSession("DOC", nativeCaps() as CapabilityMap),
    ),
    http.post(SAVE_DRAFT, async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        message: {
          status: "draft_saved",
          ackToken: ACK_TOKEN,
          encounter: {
            encounter_id: NATIVE_ENCOUNTER,
            workspace_patient_id: "PT-1",
            state: "draft",
            status: "Draft",
            modified: "2026-11-14 09:30:00.123456",
          },
        },
      });
    }),
    http.post(ACK, () => HttpResponse.json({ message: { acknowledged: true } })),
  );
  renderWithFixtures(nativeWorkspace(), {
    sessionMode: "live",
    role: "DOC",
    caps: nativeCaps() as CapabilityMap,
    path: ROUTES.visit("ENC-1"),
  });

  await user.click(await screen.findByRole("tab", { name: /หัตถการ|procedure/i }));
  await user.click(
    await screen.findByRole("button", { name: "เพิ่มหัตถการ" }),
  );
  await user.type(screen.getByLabelText("รหัสหัตถการ"), "PROC-1");
  await user.click(screen.getByRole("button", { name: "ลบ" }));
  await user.click(screen.getByRole("button", { name: "บันทึกฉบับร่าง" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body).toMatchObject({ procedures: [] });
});
