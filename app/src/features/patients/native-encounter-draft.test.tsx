import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useSession } from "../../app/SessionProvider";
import type { SessionDTO } from "../../domain/session";
import { NativeEncounterDraftError } from "../../transport/native/encounter-draft";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { PatientDirectoryPage } from "./PatientDirectoryPage";

const native = vi.hoisted(() => ({
  session: vi.fn(),
  search: vi.fn(),
  practitioner: vi.fn(),
  types: vi.fn(),
  create: vi.fn(),
}));

vi.mock("../../transport/native/session", () => ({
  fetchNativeSession: native.session,
  nativeCsrfToken: () => "csrf",
}));
vi.mock("../../transport/native/directory", () => ({
  fetchNativeDirectoryBody: async () => ({
    items: [{ workspace_patient_id: "alias-1", display_name: "Example" }],
  }),
}));
vi.mock("../../transport/native/encounter-draft", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../transport/native/encounter-draft")>(),
  searchNativeEncounterPatients: native.search,
  readNativeEncounterPractitioner: native.practitioner,
  listNativeEncounterAppointmentTypes: native.types,
  createNativeEncounterDraft: native.create,
}));

const launch = "สร้างร่างการตรวจใน Marley";
const save = "บันทึกร่าง";
const caps: SessionDTO["capabilities"] = {
  "read.patient": "enabled",
  "write.encounter.draft": "blocked_unverified",
};

function setSession(
  role: "DOC" | "REC" = "DOC",
  mode: "live" | "fixture" = "live",
  id = "doctor-a",
  capabilities = caps,
) {
  native.session.mockResolvedValue({
    mode,
    sessionEpoch: id,
    user: { id, displayName: id, roles: [role] },
    capabilities,
  } satisfies SessionDTO);
}

function RefreshSession() {
  const { refresh } = useSession();
  return (
    <button type="button" onClick={() => void refresh()}>
      Refresh session
    </button>
  );
}

function setup(role: "DOC" | "REC" = "DOC", mode: "live" | "fixture" = "live", capabilities = caps) {
  setSession(role, mode, "doctor-a", capabilities);
  return renderWithFixtures(
    <>
      <RefreshSession />
      <PatientDirectoryPage />
    </>,
    { role, sessionMode: mode, caps: capabilities },
  );
}

async function selectDetails() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: launch }));
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  await user.type(screen.getByRole("searchbox", { name: "ค้นหาผู้ป่วยใน Marley" }), "Example");
  await user.click(screen.getByRole("button", { name: "ค้นหา" }));
  await screen.findByRole("option", { name: /Example.*PAT-002/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "ผู้ป่วย" }), "PAT-002");
  await user.selectOptions(screen.getByRole("combobox", { name: "ประเภทนัดหมาย" }), "TYPE-2");
  return user;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  vi.stubEnv("VITE_CLINIC_NATIVE_ENCOUNTER_DRAFT_ENABLED", undefined);
  native.search.mockResolvedValue([
    { id: "PAT-001", label: "Example" },
    { id: "PAT-002", label: "Example" },
  ]);
  native.practitioner.mockResolvedValue({ id: "PRAC-1", label: "Doctor A" });
  native.types.mockResolvedValue([
    { id: "TYPE-1", label: "General" },
    { id: "TYPE-2", label: "Follow-up" },
  ]);
  native.create.mockResolvedValue({ id: "ENC-1" });
});

afterEach(() => vi.unstubAllEnvs());

it.each([
  [false, "live"],
  [true, "fixture"],
] as const)(
  "hides native launch: native %s, mode %s",
  async (nativeBuild, mode) => {
    vi.stubEnv("VITE_CLINIC_NATIVE", nativeBuild ? "1" : undefined);
    vi.stubEnv("VITE_CLINIC_NATIVE_ENCOUNTER_DRAFT_ENABLED", "true");
    setup("DOC", mode);
    if (!nativeBuild) {
      server.use(
        http.get("/api/live/synthetic-patients", () => HttpResponse.json({
          items: [{ workspace_patient_id: "alias-1", native_patient_id: "PAT-001", display_name: "Example" }],
        })),
      );
    }
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: launch })).not.toBeInTheDocument();
    expect(native.practitioner).not.toHaveBeenCalled();
  },
);

it("lets the live native session open the workspace and leaves authorization to Frappe", async () => {
  setup("REC", "live", { ...caps, "read.patient": "denied" });
  const user = await selectDetails();
  native.create.mockRejectedValueOnce(new NativeEncounterDraftError("denied"));
  await user.click(screen.getByRole("button", { name: save }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Marley ไม่อนุญาต");
  expect(native.create).toHaveBeenCalledTimes(1);
});

it("replaces the directory with the approved workspace and clearly marks unsupported sections", async () => {
  setup();
  const user = await selectDetails();
  const workspace = screen.getByRole("article", { name: "การตรวจผู้ป่วย" });
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Example" })).toBeVisible();
  expect(screen.getByText("PAT-002", { exact: false, selector: "p" })).toBeVisible();
  expect(within(workspace).getAllByRole("heading", { level: 3 }).map(({ textContent }) => textContent)).toEqual([
    "รายละเอียดการตรวจ", "บันทึกทางคลินิก", "ยา",
  ]);
  const rail = screen.getByRole("complementary", { name: "ข้อมูลประกอบการตรวจ" });
  expect(within(rail).getAllByRole("heading").map(({ textContent }) => textContent)).toEqual([
    "ภาพถ่ายในการตรวจครั้งนี้", "ผลแล็บ",
  ]);
  expect(screen.getByText("หน้านี้ยังไม่เชื่อมต่อการบันทึกทางคลินิก")).toBeVisible();
  expect(screen.getByText("หน้านี้ยังไม่บันทึกอาการและการวินิจฉัย")).toBeVisible();
  const more = screen.getByText("รายละเอียดเพิ่มเติม").closest("details");
  expect(more).not.toHaveAttribute("open");
  await user.click(screen.getByText("รายละเอียดเพิ่มเติม"));
  expect(within(more!).getByText("หน้านี้ยังไม่บันทึกหมายเหตุการตรวจ")).toBeVisible();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(within(rail).getByText("หน้านี้ยังไม่แสดงสถานะภาพถ่ายของการตรวจ")).toBeVisible();
  expect(within(rail).getByText("หน้านี้ยังไม่แสดงรายงานผลแล็บส่วนตัว")).toBeVisible();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /สัญญาณชีพ/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /บันทึกบันทึกทางคลินิก|เพิ่มยา|จบการตรวจ/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "กลับไปยังรายชื่อผู้ป่วย" }));
  expect(await screen.findByRole("table")).toBeVisible();
  expect(screen.queryByRole("article", { name: "การตรวจผู้ป่วย" })).not.toBeInTheDocument();
});

it("uses direct selections rather than alias/name matching and never falls back to legacy routes", async () => {
  const legacy = vi.fn();
  let finish!: (result: { id: string }) => void;
  native.create.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  setup();
  server.use(
    http.get("/api/live/encounter-drafts", () => {
      legacy();
      return HttpResponse.json({ drafts: [] });
    }),
    http.post("/api/write/encounter-draft", () => {
      legacy();
      return HttpResponse.json({});
    }),
  );
  const table = await screen.findByRole("table");
  expect(within(table).getByText("alias-1")).toBeVisible();
  expect(within(table).queryByRole("button", { name: /สร้างร่างการตรวจสำหรับ/ })).not.toBeInTheDocument();
  expect(native.practitioner).not.toHaveBeenCalled();
  const user = await selectDetails();
  expect(screen.getByText(/Doctor A.*PRAC-1/)).toBeVisible();
  expect(native.search).toHaveBeenCalledWith("Example", expect.any(AbortSignal));
  await user.dblClick(screen.getByRole("button", { name: save }));
  expect(screen.getByRole("button", { name: "กำลังบันทึกร่าง…" })).toBeDisabled();
  expect(native.create).toHaveBeenCalledExactlyOnceWith({
    userId: "doctor-a",
    patientId: "PAT-002",
    practitionerId: "PRAC-1",
    appointmentTypeId: "TYPE-2",
  });
  await act(async () => finish({ id: "ENC-1" }));
  expect(screen.getByText(/บันทึกร่างการตรวจแล้ว.*ENC-1/)).toBeVisible();
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  expect(legacy).not.toHaveBeenCalled();
});

it.each([true, false])("posts once and requires matching draft readback (readable: %s)", async (readable) => {
  const actual = await vi.importActual<typeof import("../../transport/native/encounter-draft")>(
    "../../transport/native/encounter-draft",
  );
  native.create.mockImplementation(actual.createNativeEncounterDraft);
  const requests = vi.fn();
  let finishReadback!: () => void;
  const readback = new Promise<void>((resolve) => { finishReadback = resolve; });
  server.use(
    http.post("/api/method/clinic_ui.api.encounters.create_draft", async ({ request }) => {
      requests("POST", await request.json());
      return HttpResponse.json({ message: { id: "ENC-1", docstatus: 0 } });
    }),
    http.get("/api/resource/Patient%20Encounter/ENC-1", async () => {
      requests("GET", "ENC-1");
      await readback;
      if (!readable) return HttpResponse.json({}, { status: 403 });
      return HttpResponse.json({
        data: {
          name: "ENC-1", patient: "PAT-002", practitioner: "PRAC-1", appointment_type: "TYPE-2",
          docstatus: 0, submit_orders_on_save: 0,
          drug_prescription: [], procedure_prescription: [], lab_test_prescription: [], therapies: [],
        },
      });
    }),
    http.all("/api/*", ({ request }) => {
      requests(request.method, new URL(request.url).pathname);
      return HttpResponse.json({}, { status: 404 });
    }),
  );

  setup();
  const user = await selectDetails();
  await user.dblClick(screen.getByRole("button", { name: save }));
  expect(screen.getByRole("button", { name: "กำลังบันทึกร่าง…" })).toBeDisabled();
  expect(screen.queryByText(/บันทึกร่างการตรวจแล้ว/)).not.toBeInTheDocument();
  finishReadback();

  if (readable) {
    expect(await screen.findByText(/บันทึกร่างการตรวจแล้ว.*ENC-1/)).toBeVisible();
  } else {
    expect(await screen.findByRole("alert")).toHaveTextContent("ตรวจสอบใน Marley ก่อน");
    expect(screen.queryByText(/บันทึกร่างการตรวจแล้ว/)).not.toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  expect(screen.getByText("หน้านี้ยังไม่เชื่อมต่อการบันทึกทางคลินิก")).toBeVisible();
  expect(screen.getByText("หน้านี้ยังไม่บันทึกอาการและการวินิจฉัย")).toBeVisible();
  expect(screen.getByText("หน้านี้ยังไม่รองรับการสั่งยา")).toBeVisible();
  expect(requests.mock.calls).toEqual([
    ["POST", { patient: "PAT-002", practitioner: "PRAC-1", appointment_type: "TYPE-2" }],
    ["GET", "ENC-1"],
  ]);
});

it("keeps an uncertain result visible and does not retry", async () => {
  native.create.mockRejectedValue(new NativeEncounterDraftError("unconfirmed"));
  setup();
  const user = await selectDetails();
  await user.click(screen.getByRole("button", { name: save }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ตรวจสอบใน Marley ก่อน");
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  expect(native.create).toHaveBeenCalledTimes(1);
});

it("shows a permission rejection without retrying or blocking a later explicit save", async () => {
  native.create.mockRejectedValueOnce(new NativeEncounterDraftError("denied"));
  setup();
  const user = await selectDetails();
  await user.click(screen.getByRole("button", { name: save }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Marley ไม่อนุญาต");
  expect(native.create).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: save }));
  expect(await screen.findByText(/บันทึกร่างการตรวจแล้ว.*ENC-1/)).toBeVisible();
  expect(native.create).toHaveBeenCalledTimes(2);
});

it("does not dispatch until the doctor's own practitioner is ready", async () => {
  native.practitioner.mockRejectedValue(new NativeEncounterDraftError("denied"));
  setup();
  await selectDetails();
  expect(screen.getByText(/ตรวจสอบข้อมูลผู้ตรวจไม่ได้/)).toBeVisible();
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  expect(native.create).not.toHaveBeenCalled();
});

it("clears the patient selection when the search changes", async () => {
  setup();
  const user = await selectDetails();
  const saveButton = screen.getByRole("button", { name: save });
  expect(saveButton).toBeEnabled();
  await user.clear(screen.getByRole("searchbox", { name: "ค้นหาผู้ป่วยใน Marley" }));
  expect(screen.getByRole("combobox", { name: "ผู้ป่วย" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Example" })).not.toBeInTheDocument();
  expect(saveButton).toBeDisabled();
  expect(native.create).not.toHaveBeenCalled();
});

it("discards selections and a late write result after the session identity changes", async () => {
  let finish!: (result: { id: string }) => void;
  native.create.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  setup();
  const user = await selectDetails();
  await user.click(screen.getByRole("button", { name: save }));
  setSession("DOC", "live", "doctor-b");
  native.practitioner.mockResolvedValue({ id: "PRAC-2", label: "Doctor B" });
  await user.click(screen.getByRole("button", { name: "Refresh session" }));
  await user.click(await screen.findByRole("button", { name: launch }));
  await act(async () => finish({ id: "ENC-OLD" }));
  expect(await screen.findByText(/Doctor B.*PRAC-2/)).toBeVisible();
  expect(screen.queryByText(/ENC-OLD|Doctor A/)).not.toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "ค้นหาผู้ป่วยใน Marley" })).toHaveValue("");
  expect(screen.getByRole("button", { name: save })).toBeDisabled();
  expect(native.create).toHaveBeenCalledTimes(1);
});
