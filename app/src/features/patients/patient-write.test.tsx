import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { PatientWriteRejectedError } from "../../transport/native/patient-write";
import { PatientWriteForm } from "./PatientWriteForm";

const transport = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("../../transport/native/patient-write", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../transport/native/patient-write")>(),
  saveNativePatient: transport.save,
}));

const PATIENT = {
  workspace_patient_id: "alias",
  display_name: "Test Patient",
  first_name: "Test",
  last_name: "Patient",
  sex: "Other",
  dob: "2000-01-01",
  status: "Active",
  modified: "v1",
};

beforeEach(() => {
  transport.save.mockReset();
});

afterEach(() => vi.restoreAllMocks());

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function fillCreateForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.type(screen.getByLabelText("ชื่อ"), "Test");
  await user.type(screen.getByLabelText("นามสกุล"), "Patient");
  await user.selectOptions(screen.getByLabelText("เพศ"), "Other");
  await user.type(screen.getByLabelText("วันเกิด"), "2000-01-01");
}

it("blocks invalid input before calling the write transport", async () => {
  const user = userEvent.setup();
  renderWithFixtures(<PatientWriteForm />);
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(screen.getByRole("alert")).toBeVisible();
  expect(transport.save).not.toHaveBeenCalled();
});

it("does not allow editing without a record version", async () => {
  const user = userEvent.setup();
  renderWithFixtures(
    <PatientWriteForm
      patient={{
        workspace_patient_id: "id",
        display_name: "Test",
        first_name: "Test",
        last_name: "Patient",
        sex: "Other",
        dob: "2000-01-01",
        status: "Active",
      }}
    />,
  );
  await user.click(screen.getByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }));
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
});

it("keeps the save disabled while the request is in flight and never posts twice", async () => {
  const user = userEvent.setup();
  const pending = deferred<void>();
  transport.save.mockImplementation(() => pending.promise);
  renderWithFixtures(<PatientWriteForm />);
  await fillCreateForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  const submit = screen.getByRole("button", { name: "กำลังบันทึก…" });
  expect(submit).toBeDisabled();
  await user.click(submit);
  expect(transport.save).toHaveBeenCalledTimes(1);
  pending.resolve();
  expect(await screen.findByText("บันทึกข้อมูลผู้ป่วยแล้ว")).toBeVisible();
  expect(transport.save).toHaveBeenCalledTimes(1);
});

it("reports an unknown outcome without a persistent lock across remount", async () => {
  const user = userEvent.setup();
  transport.save.mockRejectedValue(new Error("uncertain"));
  const rendered = renderWithFixtures(<PatientWriteForm />);
  await fillCreateForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ข้อมูลผู้ป่วยล่าสุด",
  );
  expect(transport.save).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0);

  rendered.unmount();
  renderWithFixtures(<PatientWriteForm />);
  expect(screen.queryByRole("alert")).toBeNull();
  await user.click(screen.getByRole("button", { name: "เพิ่มผู้ป่วยจำลอง" }));
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled();
});

it("sends the update alias and version for an editable record", async () => {
  const user = userEvent.setup();
  transport.save.mockResolvedValue(undefined);
  renderWithFixtures(<PatientWriteForm patient={PATIENT} />);
  await user.click(screen.getByRole("button", { name: "แก้ไขผู้ป่วยจำลอง" }));
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByText("บันทึกข้อมูลผู้ป่วยแล้ว")).toBeVisible();
  expect(transport.save).toHaveBeenCalledWith(
    expect.objectContaining({
      workspace_patient_id: "alias",
      base_modified: "v1",
    }),
  );
});

it("keeps a confirmed save confirmed when the record refresh fails", async () => {
  const user = userEvent.setup();
  vi.spyOn(QueryClient.prototype, "invalidateQueries").mockRejectedValue(
    new Error("refresh failed"),
  );
  transport.save.mockResolvedValue(undefined);
  renderWithFixtures(<PatientWriteForm />);
  await fillCreateForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByText("บันทึกข้อมูลผู้ป่วยแล้ว")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(transport.save).toHaveBeenCalledTimes(1);
});

it("reports an unknown outcome when both the save and the refresh fail", async () => {
  const user = userEvent.setup();
  vi.spyOn(QueryClient.prototype, "invalidateQueries").mockRejectedValue(
    new Error("refresh failed"),
  );
  transport.save.mockRejectedValue(new Error("uncertain"));
  renderWithFixtures(<PatientWriteForm />);
  await fillCreateForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ข้อมูลผู้ป่วยล่าสุด",
  );
  expect(transport.save).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled();
});

it.each([
  ["denied", 403, "FORBIDDEN", "ไม่มีสิทธิ์บันทึกข้อมูลผู้ป่วยรายนี้"],
  ["validation", 422, "VALIDATION", "เซิร์ฟเวอร์ปฏิเสธข้อมูลนี้"],
  [
    "stale",
    409,
    "STALE_VERSION",
    "ข้อมูลผู้ป่วยมีการเปลี่ยนแปลงตั้งแต่เปิดรายการนี้",
  ],
] as const)(
  "renders the known %s failure instead of an unknown outcome",
  async (kind, status, code, copy) => {
    const user = userEvent.setup();
    transport.save.mockRejectedValue(
      new PatientWriteRejectedError(kind, status, code),
    );
    renderWithFixtures(<PatientWriteForm />);
    await fillCreateForm(user);
    await user.click(screen.getByRole("button", { name: "บันทึก" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByRole("alert")).not.toHaveTextContent("ข้อมูลผู้ป่วยล่าสุด");
    expect(transport.save).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled();
  },
);

it("treats a lost response as an unknown outcome", async () => {
  const user = userEvent.setup();
  transport.save.mockRejectedValue(new Error("network"));
  renderWithFixtures(<PatientWriteForm />);
  await fillCreateForm(user);
  await user.click(screen.getByRole("button", { name: "บันทึก" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ข้อมูลผู้ป่วยล่าสุด",
  );
  expect(transport.save).toHaveBeenCalledTimes(1);
});
