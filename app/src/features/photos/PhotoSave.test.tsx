import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { renderWithCaps } from "../../test/render";
import { PhotoSave } from "./PhotoSave";

vi.mock("../../transport/photo-save", () => ({
  savePhoto: vi.fn(),
  photoStatus: vi.fn(),
}));

import { photoStatus, savePhoto } from "../../transport/photo-save";

it("keeps selection disabled without capability", () => {
  renderWithCaps({}, <PhotoSave syntheticPatient="LAB-1" />);
  expect(screen.getByLabelText("เลือกภาพสำหรับทดสอบ")).toBeDisabled();
});

it("validates selection and saves only on explicit action, then checks pending status", async () => {
  vi.mocked(savePhoto).mockResolvedValueOnce({
    status: "pending",
    operation_id: "OP-1",
  });
  vi.mocked(photoStatus).mockResolvedValueOnce({
    status: "saved",
    operation_id: "OP-1",
  });
  renderWithCaps(
    { "write.photo.upload": "enabled" },
    <PhotoSave syntheticPatient="LAB-1" />,
    { locale: "en" },
  );
  const input = screen.getByLabelText("Choose a test image");
  fireEvent.change(input, {
    target: { files: [new File(["x"], "bad.txt", { type: "text/plain" })] },
  });
  expect(screen.getByRole("alert")).toHaveTextContent(/JPEG or PNG/);
  fireEvent.change(input, {
    target: { files: [new File(["image"], "test.png", { type: "image/png" })] },
  });
  expect(savePhoto).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save test image" }));
  expect(await screen.findByText(/still processing/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  expect(await screen.findByText(/confirmed saved/)).toBeVisible();
  expect(photoStatus).toHaveBeenCalledWith("OP-1");
});

it("does not retry an uncertain write", async () => {
  vi.mocked(savePhoto).mockRejectedValueOnce(new Error("network"));
  renderWithCaps(
    { "write.photo.upload": "enabled" },
    <PhotoSave syntheticPatient="LAB-1" />,
    { locale: "en" },
  );
  fireEvent.change(screen.getByLabelText("Choose a test image"), {
    target: { files: [new File(["image"], "test.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save test image" }));
  await waitFor(() =>
    expect(screen.getByText(/outcome is unknown/)).toBeVisible(),
  );
  expect(
    screen.getByRole("button", { name: "Save test image" }),
  ).toBeDisabled();
});

it("fails closed without a verified lab patient mapping", () => {
  renderWithCaps({ "write.photo.upload": "enabled" }, <PhotoSave />, {
    locale: "en",
  });
  expect(screen.getByLabelText("Choose a test image")).toBeDisabled();
});

it("rejects oversized files and displays native unknown without retry", async () => {
  vi.mocked(savePhoto).mockResolvedValueOnce({
    status: "unknown",
    operation_id: "OP-2",
  });
  renderWithCaps(
    { "write.photo.upload": "enabled" },
    <PhotoSave syntheticPatient="LAB-1" />,
    { locale: "en" },
  );
  const input = screen.getByLabelText("Choose a test image");
  fireEvent.change(input, {
    target: {
      files: [
        new File([new Uint8Array(8 * 1024 * 1024 + 1)], "large.png", {
          type: "image/png",
        }),
      ],
    },
  });
  expect(screen.getByRole("alert")).toHaveTextContent(/8 MiB/);
  fireEvent.change(input, {
    target: { files: [new File(["image"], "test.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save test image" }));
  expect(await screen.findByText(/outcome is unknown/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Check status" })).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Save test image" }),
  ).toBeDisabled();
});
