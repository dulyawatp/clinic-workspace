import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { CapabilityMap } from "../../domain/capability";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { CREATE_DRAFT_KEY } from "./patient-create";
import { PatientCreatePage } from "./PatientCreatePage";
import { PatientProfilePage } from "./PatientProfilePage";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const VIEW = "/api/method/clinic_ui.api.patients.view";
const GENDERS = "/api/method/clinic_ui.api.patients.genders";
const UPDATE = "/api/method/clinic_ui.api.patients.update";
const DIRECTORY = "/api/method/clinic_ui.api.directory.synthetic_patients";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

function draft(sex: string) {
  return JSON.stringify({
    version: 1,
    savedAt: new Date().toISOString(),
    input: {
      firstName: "อารีย์",
      lastName: "ทดสอบ",
      sex,
      dob: "1991-02-03",
      phone: "0891234567",
      email: "",
      allergyStatus: "unknown",
      allergyText: "",
      address: "",
      emergency: "",
    },
  });
}

function createdPatient() {
  return HttpResponse.json({
    message: {
      status: "created",
      patient: {
        workspace_patient_id: "PAT-1",
        display_name: "อารีย์ ทดสอบ",
        first_name: "อารีย์",
        last_name: "ทดสอบ",
        sex: "Non-binary",
        dob: "1991-02-03",
        status: "Active",
        modified: "v1",
      },
    },
  });
}

function session(caps: CapabilityMap) {
  return HttpResponse.json({
    message: {
      mode: "live",
      sessionEpoch: "genders-epoch",
      user: { id: "u", displayName: "User", roles: ["DOC"] },
      capabilities: caps,
      csrfToken: "csrf",
    },
  });
}

function detail(sex: string) {
  return HttpResponse.json({
    message: {
      workspace_patient_id: "WS-1",
      display_name: "Patient One",
      first_name: "Patient",
      last_name: "One",
      sex,
      dob: "1990-01-02",
      status: "Active",
      modified: "version",
      editable: true,
    },
  });
}

function optionValues(select: HTMLElement): string[] {
  return Array.from((select as HTMLSelectElement).options).map(
    (option) => option.value,
  );
}

function renderProfile() {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/patients/:id" element={<PatientProfilePage />} />
    </Routes>,
    { path: "/clinic/patients/WS-1" },
  );
}

it("offers only the sex values the isolated site returns on the native update form", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  renderProfile();
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.update": "enabled" }),
    ),
    http.get(VIEW, () => detail("Female")),
    http.get(GENDERS, () =>
      HttpResponse.json({ message: { items: ["Female"] } }),
    ),
  );

  await user.click(
    await screen.findByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }),
  );
  const select = await screen.findByLabelText("เพศ");
  await waitFor(() => expect(optionValues(select)).toEqual(["", "Female"]));
  expect(select).toHaveValue("Female");
});

it("clears a stored sex the site no longer offers and blocks the write", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  const writes: string[] = [];
  renderProfile();
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.update": "enabled" }),
    ),
    http.get(VIEW, () => detail("Male")),
    http.get(GENDERS, () =>
      HttpResponse.json({ message: { items: ["Female"] } }),
    ),
    http.post(UPDATE, () => {
      writes.push("update");
      return HttpResponse.json({ message: { status: "updated" } });
    }),
  );

  await user.click(
    await screen.findByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }),
  );
  const select = await screen.findByLabelText("เพศ");
  await waitFor(() => expect(select).toHaveValue(""));

  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "กรุณาระบุชื่อ นามสกุล เพศ และวันเกิดที่ถูกต้องและไม่ใช่วันในอนาคต",
  );
  expect(writes).toHaveLength(0);
});

it("blocks the native update form while sex options are still loading", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  renderProfile();
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.update": "enabled" }),
    ),
    http.get(VIEW, () => detail("Female")),
    http.get(GENDERS, async () => {
      await delay("infinite");
      return HttpResponse.json({ message: { items: ["Female"] } });
    }),
  );

  await user.click(
    await screen.findByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }),
  );
  expect(await screen.findByLabelText("เพศ")).toBeDisabled();
  expect(await screen.findByText("กำลังโหลดตัวเลือกเพศ…")).toBeVisible();
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
});

it("reports a failed sex-option load and blocks the write", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const user = userEvent.setup();
  renderProfile();
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.update": "enabled" }),
    ),
    http.get(VIEW, () => detail("Female")),
    http.get(GENDERS, () => HttpResponse.json({}, { status: 500 })),
  );

  await user.click(
    await screen.findByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ตรวจสอบการเชื่อมต่อหรือสิทธิ์การใช้งาน",
  );
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
});

it("shows loading and blocks native create while sex options load", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.create": "enabled" }),
    ),
    http.get(DIRECTORY, () => HttpResponse.json({ message: { items: [] } })),
    http.get(GENDERS, async () => {
      await delay("infinite");
      return HttpResponse.json({ message: { items: ["Female"] } });
    }),
  );

  expect(await screen.findByText("กำลังโหลดตัวเลือกเพศ…")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
  ).toBeDisabled();
});

it("blocks native create and reports an empty sex-option list", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.create": "enabled" }),
    ),
    http.get(DIRECTORY, () => HttpResponse.json({ message: { items: [] } })),
    http.get(GENDERS, () => HttpResponse.json({ message: { items: [] } })),
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ไม่สามารถโหลดตัวเลือกเพศได้",
  );
  expect(
    screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
  ).toBeDisabled();
});

it("blocks native create when the sex-option load fails", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.create": "enabled" }),
    ),
    http.get(DIRECTORY, () => HttpResponse.json({ message: { items: [] } })),
    http.get(GENDERS, () => HttpResponse.json({}, { status: 500 })),
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ตรวจสอบการเชื่อมต่อหรือสิทธิ์การใช้งาน",
  );
  expect(
    screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }),
  ).toBeDisabled();
});

it("restores a custom native gender draft and sends it when the site returns it", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(CREATE_DRAFT_KEY, draft("Non-binary"));
  const bodies: Record<string, unknown>[] = [];
  const user = userEvent.setup();
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.create": "enabled" }),
    ),
    http.get(DIRECTORY, () => HttpResponse.json({ message: { items: [] } })),
    http.get(GENDERS, () =>
      HttpResponse.json({ message: { items: ["Non-binary"] } }),
    ),
    http.post("/api/method/clinic_ui.api.patients.create", async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return createdPatient();
    }),
  );

  const select = await screen.findByLabelText("เพศ");
  await waitFor(() => expect(select).toHaveValue("Non-binary"));
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  await user.click(
    await screen.findByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  );
  expect(await screen.findByText("สร้างข้อมูลผู้ป่วยแล้ว")).toBeVisible();
  expect(bodies).toHaveLength(1);
  expect(bodies[0]?.sex).toBe("Non-binary");
});

it("blocks a custom gender draft the site does not return", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(CREATE_DRAFT_KEY, draft("Non-binary"));
  const bodies: string[] = [];
  const user = userEvent.setup();
  renderWithFixtures(<PatientCreatePage />, {
    sessionMode: "live",
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });
  server.use(
    http.get(BOOTSTRAP, () =>
      session({ "read.patient": "enabled", "write.patient.create": "enabled" }),
    ),
    http.get(DIRECTORY, () => HttpResponse.json({ message: { items: [] } })),
    http.get(GENDERS, () =>
      HttpResponse.json({ message: { items: ["Female"] } }),
    ),
    http.post("/api/method/clinic_ui.api.patients.create", () => {
      bodies.push("create");
      return createdPatient();
    }),
  );

  const select = await screen.findByLabelText("เพศ");
  await waitFor(() =>
    expect(optionValues(select)).toEqual(["", "Female"]),
  );
  await user.click(screen.getByRole("button", { name: "ตรวจสอบและลงทะเบียน" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "เลือกเพศจากตัวเลือกที่เว็บไซต์นี้ใช้ได้",
  );
  expect(
    screen.queryByRole("button", { name: "ลงทะเบียนผู้ป่วย" }),
  ).toBeNull();
  expect(bodies).toHaveLength(0);
});

it("keeps the fixture and proxy create form choices unchanged", async () => {
  const genderRequests: string[] = [];
  server.events.on("request:start", (event) => {
    if (event.request.url.includes("patients.genders"))
      genderRequests.push(event.request.url);
  });
  renderWithFixtures(<PatientCreatePage />, {
    caps: { "read.patient": "enabled", "write.patient.create": "enabled" },
  });

  const select = await screen.findByLabelText("เพศ");
  await waitFor(() =>
    expect(optionValues(select)).toEqual(["", "female", "male", "other"]),
  );
  expect(genderRequests).toHaveLength(0);
  server.events.removeAllListeners("request:start");
});
