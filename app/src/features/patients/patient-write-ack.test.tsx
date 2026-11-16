import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { PatientWriteForm } from "./PatientWriteForm";

vi.mock("../../transport/native/session", async (original) => ({
  ...await original<typeof import("../../transport/native/session")>(),
  nativeCsrfToken: () => "csrf",
}));

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it.each(["409", "connection", "invalid"])("keeps save disabled across remount after ack %s", async (failure) => {
  const user = userEvent.setup();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    if (String(url).endsWith(".create")) return new Response(JSON.stringify({ message: {
      status: "created", ackToken: "a".repeat(64), patient: {
        workspace_patient_id: "alias", display_name: "Test Patient", first_name: "Test",
        last_name: "Patient", sex: "Other", dob: "2000-01-01", status: "Active", modified: "v1",
      },
    } }), { status: 200 });
    if (failure === "connection") throw new Error("dropped");
    return new Response(JSON.stringify({ message: { acknowledged: failure !== "invalid" } }), { status: failure === "409" ? 409 : 200 });
  });
  const rendered = renderWithFixtures(<PatientWriteForm />);
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  await user.selectOptions(screen.getByLabelText("เพศ"), "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("อย่าลองอีกครั้ง");
  rendered.unmount();
  renderWithFixtures(<PatientWriteForm />);
  expect(screen.getByRole("alert")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/method/clinic_ui.api.patients."))).toHaveLength(2);
});
