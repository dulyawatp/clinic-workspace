import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { PatientDirectoryPage } from "./PatientDirectoryPage";
import { PatientProfilePage } from "./PatientProfilePage";

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

const NATIVE_GENDERS = "/api/method/clinic_ui.api.patients.genders";

/** The isolated site offers only these values; there is no "Male". */
function nativeGenders() {
  return http.get(NATIVE_GENDERS, () =>
    HttpResponse.json({ message: { items: ["Female", "Other"] } }),
  );
}

it.each([
  ["enabled", "read_only", true, false],
  ["read_only", "enabled", false, true],
  ["read_only", "read_only", false, false],
  ["enabled", "enabled", true, true],
] as const)("shows native create/edit only for each enabled capability (%s, %s)", async (create, update, showCreate, showEdit) => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  renderWithFixtures(<Routes>
    <Route path="/" element={<PatientDirectoryPage />} />
    <Route path="/clinic/patients/:id" element={<PatientProfilePage />} />
  </Routes>, { path: "/" });
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({ message: {
      mode: "live", sessionEpoch: "epoch", user: { id: "u", displayName: "User", roles: ["DOC"] },
      capabilities: { "read.patient": "enabled", "write.patient.create": create, "write.patient.update": update }, csrfToken: "csrf",
    } })),
    http.get("/api/method/clinic_ui.api.directory.synthetic_patients", () => HttpResponse.json({ message: { items: [{ workspace_patient_id: "WS-1", display_name: "Patient One" }] } })),
    http.get("/api/method/clinic_ui.api.patients.view", () => HttpResponse.json({ message: {
      workspace_patient_id: "WS-1", display_name: "Patient One", first_name: "Patient", last_name: "One",
      sex: "Female", dob: "1990-01-02", status: "Active", modified: "version", editable: true,
    } })),
  );
  await screen.findByText("Patient One");
  expect(Boolean(screen.queryByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }))).toBe(showCreate);
  const link = screen.getByRole("link", { name: /Patient One/ });
  await userEvent.click(link);
  await screen.findByRole("heading", { name: "Patient One" });
  expect(Boolean(screen.queryByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }))).toBe(showEdit);
});

it.each([false, undefined])("keeps profile visible without edit when editable is %s", async (editable) => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  renderWithFixtures(<Routes><Route path="/clinic/patients/:id" element={<PatientProfilePage />} /></Routes>, { path: "/clinic/patients/WS-1" });
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({ message: {
      mode: "live", sessionEpoch: "epoch", user: { id: "other", displayName: "Other", roles: ["DOC"] },
      capabilities: { "read.patient": "enabled", "write.patient.update": "enabled" }, csrfToken: "csrf",
    } })),
    http.get("/api/method/clinic_ui.api.patients.view", () => HttpResponse.json({ message: {
      workspace_patient_id: "WS-1", display_name: "Patient One", first_name: "Patient", last_name: "One",
      sex: "Female", dob: "1990-01-02", status: "Active", modified: "version",
      ...(editable === undefined ? {} : { editable }),
    } })),
  );
  expect(await screen.findByRole("heading", { name: "Patient One" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" })).toBeNull();
  if (editable === false)
    expect(screen.getByText("ไม่สามารถแก้ไขข้อมูลรายการนี้ได้")).toHaveAttribute("role", "status");
  else
    expect(screen.queryByText("ไม่สามารถแก้ไขข้อมูลรายการนี้ได้")).toBeNull();
});

it("refreshes the native directory after a confirmed creation without an acknowledgement", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.clear();
  let created = false;
  let directoryReads = 0;
  const requests: string[] = [];
  renderWithFixtures(<PatientDirectoryPage />);
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({ message: {
      mode: "live", sessionEpoch: "epoch", user: { id: "u", displayName: "User", roles: ["DOC"] },
      capabilities: { "read.patient": "enabled", "write.patient.create": "enabled" }, csrfToken: "csrf",
    } })),
    http.get("/api/method/clinic_ui.api.directory.synthetic_patients", () => {
      directoryReads += 1;
      return HttpResponse.json({ message: { items: created ? [{ workspace_patient_id: "WS-new", display_name: "Test Patient" }] : [] } });
    }),
    http.post("/api/method/clinic_ui.api.patients.create", () => {
      requests.push("create");
      created = true;
      // Canonical backend fields need not repeat what the form submitted.
      return HttpResponse.json({ message: { status: "created", patient: {
        workspace_patient_id: "PAT-0001", display_name: "TEST PATIENT", first_name: "TEST", last_name: "PATIENT", sex: "Female", dob: "2000-01-01", status: "Active", modified: "v1",
      } } });
    }),
    http.post("/api/method/clinic_ui.api.patients.ack", () => {
      requests.push("ack");
      return HttpResponse.json({ message: { acknowledged: true } });
    }),
    nativeGenders(),
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  const sex = screen.getByLabelText("เพศ");
  await waitFor(() =>
    expect(
      Array.from((sex as HTMLSelectElement).options).map(
        (option) => option.value,
      ),
    ).toContain("Other"),
  );
  await user.selectOptions(sex, "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByText("Test Patient")).toBeVisible();
  expect(directoryReads).toBe(2);
  expect(requests).toEqual(["create"]);
});

it("renders a stale refusal as a known failure without retrying", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.clear();
  let directoryReads = 0;
  const creates: string[] = [];
  renderWithFixtures(<PatientDirectoryPage />);
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({ message: {
      mode: "live", sessionEpoch: "epoch", user: { id: "u", displayName: "User", roles: ["DOC"] },
      capabilities: { "read.patient": "enabled", "write.patient.create": "enabled" }, csrfToken: "csrf",
    } })),
    http.get("/api/method/clinic_ui.api.directory.synthetic_patients", () => {
      directoryReads += 1;
      return HttpResponse.json({ message: { items: [] } });
    }),
    http.post("/api/method/clinic_ui.api.patients.create", () => {
      creates.push("create");
      return HttpResponse.json({ message: { code: "STALE_VERSION" } }, { status: 409 });
    }),
    nativeGenders(),
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  const sex = screen.getByLabelText("เพศ");
  await waitFor(() =>
    expect(
      Array.from((sex as HTMLSelectElement).options).map(
        (option) => option.value,
      ),
    ).toContain("Other"),
  );
  await user.selectOptions(sex, "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ข้อมูลผู้ป่วยมีการเปลี่ยนแปลงตั้งแต่เปิดรายการนี้");
  expect(screen.getByRole("alert")).not.toHaveTextContent("ยังไม่ยืนยันผลการบันทึก");
  expect(directoryReads).toBe(2);
  expect(creates).toHaveLength(1);
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled();
});

it("reports an unknown outcome and refreshes records before a repeat", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.clear();
  let directoryReads = 0;
  const creates: string[] = [];
  renderWithFixtures(<PatientDirectoryPage />);
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () => HttpResponse.json({ message: {
      mode: "live", sessionEpoch: "epoch", user: { id: "u", displayName: "User", roles: ["DOC"] },
      capabilities: { "read.patient": "enabled", "write.patient.create": "enabled" }, csrfToken: "csrf",
    } })),
    http.get("/api/method/clinic_ui.api.directory.synthetic_patients", () => {
      directoryReads += 1;
      return HttpResponse.json({ message: { items: [] } });
    }),
    http.post("/api/method/clinic_ui.api.patients.create", () => {
      creates.push("create");
      // Malformed success: the browser cannot tell whether the row was written.
      return HttpResponse.json({ message: { status: "created" } });
    }),
    nativeGenders(),
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  const sex = screen.getByLabelText("เพศ");
  await waitFor(() =>
    expect(
      Array.from((sex as HTMLSelectElement).options).map(
        (option) => option.value,
      ),
    ).toContain("Other"),
  );
  await user.selectOptions(sex, "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ข้อมูลผู้ป่วยล่าสุด");
  expect(directoryReads).toBe(2);
  expect(creates).toHaveLength(1);
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled();
});
