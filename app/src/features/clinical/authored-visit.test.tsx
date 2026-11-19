import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import type { CapabilityMap } from "../../domain/capability";
import { installDialogMethods } from "../../test/dialog";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AuthoredVisitWorkspace } from "./AuthoredVisitWorkspace";

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

it("explains native medication deferral and allows lab entry without sending medication rows", async () => {
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
  expect(await screen.findByText(/ไม่สามารถสั่งยา/)).toBeVisible();
  expect(screen.getByText(/รายการยาเดิมจะไม่แสดง/)).toHaveTextContent(
    "การตรวจที่มีรายการยาเดิมไม่สามารถบันทึกหรือจบการตรวจในหน้านี้ได้ โปรดขอความช่วยเหลือจากเจ้าหน้าที่",
  );
  expect(screen.queryByRole("button", { name: "เพิ่มยา" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("รหัสยา")).not.toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /ผลแล็บ|lab/i }));
  await user.click(
    await screen.findByRole("button", { name: "สั่งตรวจแล็บ" }),
  );
  await user.type(screen.getByLabelText("รหัสตรวจแล็บ"), "CBC");

  await user.click(screen.getByRole("button", { name: "บันทึกฉบับร่าง" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body).toMatchObject({
    medications: [],
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
