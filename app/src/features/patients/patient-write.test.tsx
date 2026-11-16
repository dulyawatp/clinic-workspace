import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { PatientWriteForm } from "./PatientWriteForm";

const transport = vi.hoisted(() => ({ locked: false, save: vi.fn() }));
vi.mock("../../transport/native/patient-write", () => ({
  patientWriteLocked: () => transport.locked,
  saveNativePatient: transport.save,
}));

beforeEach(() => {
  transport.locked = false;
  transport.save.mockReset();
});

it("blocks invalid input before calling the write transport", async () => {
  const { saveNativePatient } = await import("../../transport/native/patient-write");
  const user = userEvent.setup();
  renderWithFixtures(<PatientWriteForm />);
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(screen.getByRole("alert")).toBeVisible();
  expect(saveNativePatient).not.toHaveBeenCalled();
});

it("does not allow editing without a record version", async () => {
  const user = userEvent.setup();
  renderWithFixtures(<PatientWriteForm patient={{ workspace_patient_id: "id", display_name: "Test", first_name: "Test", last_name: "Patient", sex: "Other", dob: "2000-01-01", status: "Active" }} />);
  await user.click(screen.getByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }));
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
});

it("shows persistent uncertainty and disables save after remount", async () => {
  const user = userEvent.setup();
  transport.save.mockImplementation(async () => {
    transport.locked = true;
    throw new Error("uncertain");
  });
  const rendered = renderWithFixtures(<PatientWriteForm />);
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  await user.selectOptions(screen.getByLabelText("เพศ"), "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("อย่าลองอีกครั้ง");
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
  rendered.unmount();
  renderWithFixtures(<PatientWriteForm />);
  expect(screen.getByRole("alert")).toHaveTextContent("อย่าลองอีกครั้ง");
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
  expect(transport.save).toHaveBeenCalledTimes(1);
});
