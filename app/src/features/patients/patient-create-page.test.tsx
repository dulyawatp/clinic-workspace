import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import type { CapabilityMap } from "../../domain/capability";
import { server } from "../../test/msw/server";
import {
  renderWithDataRouter,
  renderWithFixtures,
  setViewport,
} from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";

const nativeWrite = vi.hoisted(() => ({ save: vi.fn(), locked: false }));
vi.mock("../../transport/native/patient-write", () => ({
  patientWriteLocked: () => nativeWrite.locked,
  saveNativePatient: nativeWrite.save,
}));
import {
  CREATE_DRAFT_KEY,
  CREATE_SAMPLES_KEY,
  type PatientCreateInput,
} from "./patient-create";
import { PatientDirectoryPage } from "./PatientDirectoryPage";
import { PatientCreatePage } from "./PatientCreatePage";

const CREATE_CAPS: CapabilityMap = {
  "read.patient": "enabled",
  "write.patient.create": "enabled",
};

afterEach(() => {
  localStorage.removeItem(CREATE_DRAFT_KEY);
  localStorage.removeItem(CREATE_SAMPLES_KEY);
  vi.unstubAllEnvs();
  nativeWrite.save.mockReset();
  nativeWrite.locked = false;
  clearNativeCsrfToken();
});

function renderPage(caps: CapabilityMap = {}) {
  return renderWithFixtures(<PatientCreatePage />, { caps });
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("ชื่อ"), "อารีย์");
  await user.type(screen.getByLabelText("นามสกุล"), "ทดสอบ");
  await user.selectOptions(screen.getByLabelText("เพศ"), "female");
  await user.type(screen.getByLabelText("วันเกิด"), "1991-02-03");
  await user.type(screen.getByLabelText("เบอร์โทรศัพท์"), "0891234567");
}

it("renders the designed ten-field create form and the deferred consent group", async () => {
  renderPage();
  expect(
    await screen.findByRole("heading", { level: 1, name: "ลงทะเบียนผู้ป่วย" }),
  ).toBeVisible();
  for (const label of [
    "ชื่อ",
    "นามสกุล",
    "เพศ",
    "วันเกิด",
    "เบอร์โทรศัพท์",
    "อีเมล (ไม่จำเป็น)",
    "สถานะการแพ้ยา",
    "ชื่อยาที่แพ้ (ถ้ามี)",
    "ที่อยู่ (ไม่จำเป็น)",
    "ผู้ติดต่อฉุกเฉิน (ชื่อ ความสัมพันธ์ เบอร์)",
  ])
    expect(screen.getByLabelText(label)).toBeVisible();
  const consent = screen.getByRole("group", { name: "ความยินยอมถ่ายภาพ" });
  expect(within(consent).getByText(/เก็บแยกในขั้นตอนรับภาพ/)).toBeVisible();
  expect(screen.getAllByTestId("sample-banner").length).toBeGreaterThan(0);
});

it("blocks invalid input with an error summary and no write", async () => {
  const user = userEvent.setup();
  const posts = vi.fn();
  server.events.on("request:start", (event) => {
    if (event.request.method !== "GET") posts(event.request.url);
  });
  renderPage();
  await user.click(
    await screen.findByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
  );
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("ตรวจสอบข้อมูลก่อนลงทะเบียน");
  expect(screen.getByLabelText("เบอร์โทรศัพท์")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(posts).not.toHaveBeenCalled();
  server.events.removeAllListeners("request:start");
});

it("validates mobile, future birth date and known-allergy name", async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(await screen.findByLabelText("ชื่อ"), "อารีย์");
  await user.type(screen.getByLabelText("นามสกุล"), "ทดสอบ");
  await user.selectOptions(screen.getByLabelText("เพศ"), "female");
  await user.type(screen.getByLabelText("วันเกิด"), "2999-01-01");
  await user.type(screen.getByLabelText("เบอร์โทรศัพท์"), "12345");
  await user.selectOptions(screen.getByLabelText("สถานะการแพ้ยา"), "known");
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("เบอร์โทรต้องมี 9–10 หลัก");
  expect(alert).toHaveTextContent("วันเกิดต้องไม่เป็นอนาคต");
  expect(alert).toHaveTextContent("ระบุชื่อยาที่แพ้");
});

it("detects a duplicate directory candidate before any registration", async () => {
  const user = userEvent.setup();
  renderPage();
  await user.type(await screen.findByLabelText("ชื่อ"), "สมชาย");
  await user.type(screen.getByLabelText("นามสกุล"), "ใจดี");
  await user.selectOptions(screen.getByLabelText("เพศ"), "male");
  await user.type(screen.getByLabelText("วันเกิด"), "1985-04-12");
  await user.type(screen.getByLabelText("เบอร์โทรศัพท์"), "0891234567");
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  const warning = await screen.findByRole("alert");
  expect(warning).toHaveTextContent("อาจเป็นผู้ป่วยซ้ำ");
  const existing = within(warning).getByRole("link", {
    name: /สมชาย ใจดี/,
  });
  expect(existing).toHaveAttribute("href", "/clinic/patients/fixture-dir-01");
  expect(
    within(warning).getByRole("button", { name: "ลงทะเบียนต่อ" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  ).toBeNull();
});

it("fails closed when the duplicate check cannot read the directory", async () => {
  const user = userEvent.setup();
  renderPage();
  server.use(
    http.get("*/api/read/frontdesk-patients", () =>
      HttpResponse.json({ code: "UPSTREAM_UNAVAILABLE" }, { status: 503 }),
    ),
  );
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(
    await screen.findByText("ตรวจสอบรายการซ้ำไม่ได้ จึงยังไม่เปิดให้ลงทะเบียน"),
  ).toBeVisible();
});

it("keeps a local draft across a remount and clears it on request", async () => {
  const user = userEvent.setup();
  const first = renderPage();
  await user.type(await screen.findByLabelText("ชื่อ"), "อารีย์");
  await user.type(
    screen.getByLabelText("ผู้ติดต่อฉุกเฉิน (ชื่อ ความสัมพันธ์ เบอร์)"),
    "ปวีณา · พี่สาว · 089-000-1111",
  );
  await user.click(screen.getByRole("button", { name: "บันทึกฉบับร่าง" }));
  expect(await screen.findByText("เก็บฉบับร่างในเครื่องแล้ว")).toBeVisible();
  first.unmount();

  renderPage();
  expect(await screen.findByText("โหลดฉบับร่างในเครื่องแล้ว")).toBeVisible();
  expect(screen.getByLabelText("ชื่อ")).toHaveValue("อารีย์");
  expect(
    screen.getByLabelText("ผู้ติดต่อฉุกเฉิน (ชื่อ ความสัมพันธ์ เบอร์)"),
  ).toHaveValue("ปวีณา · พี่สาว · 089-000-1111");
  await user.click(screen.getByRole("button", { name: "ล้างฉบับร่าง" }));
  await waitFor(() =>
    expect(screen.getByLabelText("ชื่อ")).toHaveValue(""),
  );
});

it("gates the real registration action exactly on write.patient.create", async () => {
  const user = userEvent.setup();
  renderPage();
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  const register = await screen.findByRole("button", {
    name: "ลงทะเบียนผู้ป่วย",
  });
  expect(register).toBeDisabled();
  expect(
    screen.getByText(/การบันทึกผู้ป่วยยังไม่เปิดใช้งานในสัญญานี้/),
  ).toBeVisible();
});

it("stores a labelled sample registration locally when the capability is enabled", async () => {
  const user = userEvent.setup();
  const posts = vi.fn();
  server.events.on("request:start", (event) => {
    if (event.request.method !== "GET") posts(event.request.url);
  });
  renderPage(CREATE_CAPS);
  await fillValidForm(user);
  await user.type(
    screen.getByLabelText("ผู้ติดต่อฉุกเฉิน (ชื่อ ความสัมพันธ์ เบอร์)"),
    "ปวีณา · พี่สาว · 089-000-1111",
  );
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  const register = await screen.findByRole("button", {
    name: "ลงทะเบียนผู้ป่วย",
  });
  expect(register).toBeEnabled();
  await user.click(register);
  expect(await screen.findByText(/บันทึกตัวอย่างในเครื่องแล้ว/)).toBeVisible();
  expect(posts).not.toHaveBeenCalled();
  const rows = JSON.parse(
    localStorage.getItem(CREATE_SAMPLES_KEY) ?? "[]",
  ) as Array<{ details: PatientCreateInput }>;
  expect(rows[0]?.details.emergency).toBe("ปวีณา · พี่สาว · 089-000-1111");
  server.events.removeAllListeners("request:start");
});

it.each([390, 834, 1440] as const)(
  "keeps the create form and its error panel in flow at %spx",
  async (width) => {
    setViewport(width);
    const user = userEvent.setup();
    renderPage();
    const form = await screen.findByRole("form");
    expect(form).toHaveClass("patient-create-form");
    expect(form.closest(".pc-card")).not.toBeNull();
    // No fixed-width column can force the page to scroll sideways: every group
    // is a shrinking grid cell.
    expect(
      Array.from(form.querySelectorAll(".patient-create-group")).every(
        (group) => group.closest(".patient-create-form") === form,
      ),
    ).toBe(true);
    await user.click(
      screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.closest(".patient-create-form")).not.toBeNull();
  },
);

const NATIVE_BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const NATIVE_DIRECTORY = "/api/method/clinic_ui.api.directory.synthetic_patients";

function nativeSession(caps: CapabilityMap) {
  return HttpResponse.json({
    message: {
      mode: "live",
      sessionEpoch: "native-epoch",
      user: {
        id: "native|rec",
        displayName: "Native Reception",
        roles: ["REC"],
      },
      capabilities: caps,
      csrfToken: "native-csrf",
    },
  });
}

it("keeps sample, draft and duplicate states network-free in native mode", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  const posts = vi.fn();
  server.events.on("request:start", (event) => {
    if (event.request.method !== "GET") posts(event.request.url);
  });
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession({ "read.patient": "enabled" })),
    http.get(NATIVE_DIRECTORY, () =>
      HttpResponse.json({
        message: { items: [{ workspace_patient_id: "WS-1", display_name: "Native Patient" }] },
      }),
    ),
  );
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled" },
  });
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึกฉบับร่าง" }));
  expect(await screen.findByText("เก็บฉบับร่างในเครื่องแล้ว")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(
    await screen.findByText(/การบันทึกผู้ป่วยยังไม่เปิดใช้งานในสัญญานี้/),
  ).toBeVisible();
  expect(posts).not.toHaveBeenCalled();
  expect(nativeWrite.save).not.toHaveBeenCalled();
  server.events.removeAllListeners("request:start");
});

it("writes only the current native contract fields when it is enabled", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      nativeSession({
        "read.patient": "enabled",
        "write.patient.create": "enabled",
      }),
    ),
    http.get(NATIVE_DIRECTORY, () =>
      HttpResponse.json({
        message: {
          items: [{ workspace_patient_id: "WS-1", display_name: "Native Patient" }],
        },
      }),
    ),
  );
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: {
      "read.patient": "enabled",
      "write.patient.create": "enabled",
    },
  });
  await fillValidForm(user);
  await user.type(
    screen.getByLabelText("ผู้ติดต่อฉุกเฉิน (ชื่อ ความสัมพันธ์ เบอร์)"),
    "ปวีณา · 089-000-1111",
  );
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  await user.click(
    await screen.findByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  );
  expect(await screen.findByText("สร้างข้อมูลผู้ป่วยแล้ว")).toBeVisible();
  expect(nativeWrite.save).toHaveBeenCalledTimes(1);
  const payload = nativeWrite.save.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(Object.keys(payload).sort()).toEqual([
    "dob",
    "first_name",
    "last_name",
    "operation_key",
    "sex",
  ]);
  expect(payload).toMatchObject({
    first_name: "อารีย์",
    last_name: "ทดสอบ",
    sex: "Female",
    dob: "1991-02-03",
  });
});

it.each(["REC", "ADM"] as const)(
  "opens the create route for the designed %s role",
  async (role) => {
    renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.patientNew],
      role,
    });
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "ลงทะเบียนผู้ป่วย",
      }),
    ).toBeVisible();
  },
);

it("denies the create route to a role outside the designed list", async () => {
  renderWithDataRouter(appRoutes, {
    initialEntries: [ROUTES.patientNew],
    role: "DOC",
  });
  expect(await screen.findByTestId("role-guard-denied")).toBeVisible();
});

it("navigates from the directory register action when the capability is enabled", async () => {
  const user = userEvent.setup();
  renderWithFixtures(
    <Routes>
      <Route path={ROUTES.patients} element={<PatientDirectoryPage />} />
      <Route path={ROUTES.patientNew} element={<PatientCreatePage />} />
    </Routes>,
    { caps: CREATE_CAPS, path: ROUTES.patients },
  );
  await user.click(
    await screen.findByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  );
  expect(
    await screen.findByRole("heading", {
      level: 1,
      name: "ลงทะเบียนผู้ป่วย",
    }),
  ).toBeVisible();
});

it("keeps the native write closed after an uncertain result", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  nativeWrite.save.mockImplementation(async () => {
    nativeWrite.locked = true;
    throw new Error("uncertain");
  });
  server.use(
    http.get(NATIVE_BOOTSTRAP, () =>
      nativeSession({
        "read.patient": "enabled",
        "write.patient.create": "enabled",
      }),
    ),
    http.get(NATIVE_DIRECTORY, () =>
      HttpResponse.json({
        message: {
          items: [{ workspace_patient_id: "WS-1", display_name: "Native Patient" }],
        },
      }),
    ),
  );
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: {
      "read.patient": "enabled",
      "write.patient.create": "enabled",
    },
  });
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  const register = await screen.findByRole("button", {
    name: "ลงทะเบียนผู้ป่วย",
  });
  await user.click(register);
  expect(await screen.findByRole("alert")).toHaveTextContent("อย่าลองอีกครั้ง");
  expect(
    screen.getByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  ).toBeDisabled();
  expect(nativeWrite.save).toHaveBeenCalledTimes(1);
});

it("drops the confirm step as soon as a reviewed field changes", async () => {
  const user = userEvent.setup();
  renderPage(CREATE_CAPS);
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(
    await screen.findByRole("heading", { name: "ยืนยันลงทะเบียน" }),
  ).toBeVisible();

  await user.clear(screen.getByLabelText("ชื่อ"));
  expect(
    screen.queryByRole("heading", { name: "ยืนยันลงทะเบียน" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
  ).toBeVisible();

  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "กรอกข้อมูลที่จำเป็นให้ครบ",
  );
});

it("re-validates a changed phone instead of registering the reviewed snapshot", async () => {
  const user = userEvent.setup();
  renderPage(CREATE_CAPS);
  await fillValidForm(user);
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  await screen.findByRole("heading", { name: "ยืนยันลงทะเบียน" });

  const phone = screen.getByLabelText("เบอร์โทรศัพท์");
  await user.clear(phone);
  await user.type(phone, "12345");
  expect(
    screen.queryByRole("heading", { name: "ยืนยันลงทะเบียน" }),
  ).toBeNull();

  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "เบอร์โทรต้องมี 9–10 หลัก",
  );
  expect(
    screen.queryByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  ).toBeNull();
  expect(localStorage.getItem(CREATE_SAMPLES_KEY)).toBeNull();
});
