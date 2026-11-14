import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import type { CapabilityMap } from "../../domain/capability";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { photoStatus, savePhoto } from "../../transport/photo-save";
import { LivePhotoWorkspace } from "./LivePhotoWorkspace";

vi.mock("../../transport/photo-save", () => ({
  savePhoto: vi.fn(),
  photoStatus: vi.fn(),
}));

function renderPage(
  path = "/clinic/lab/photos",
  caps: CapabilityMap = { "write.photo.upload": "enabled" },
  mode: "live" | "fixture" = "live",
  role: "DOC" | "NUR" | "REC" = "DOC",
) {
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/lab/photos/:id?" element={<LivePhotoWorkspace />} />
    </Routes>,
    { role, path, caps, sessionMode: mode },
  );
  server.use(
    http.get("*/api/live/synthetic-patients/:reference/photos", () =>
      HttpResponse.json({ items: [] }),
    ),
  );
}

it("shows live photos while writes are disabled", async () => {
  renderPage("/clinic/lab/photos/workspace-1", {
    "write.photo.upload": "read_only",
  });
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
  );
  expect(await screen.findByLabelText("เลือกภาพสำหรับทดสอบ")).toBeDisabled();
  expect(screen.getByText(/ไม่สามารถบันทึกได้/)).toBeVisible();
  expect(await screen.findByText("ไม่พบภาพที่บันทึกในหน้านี้")).toBeVisible();
});

it("does not request live patients from fixture mode", async () => {
  const read = vi.fn(() => HttpResponse.json({ items: [] }));
  renderPage(
    "/clinic/lab/photos",
    { "write.photo.upload": "enabled" },
    "fixture",
  );
  server.use(http.get("*/api/live/synthetic-patients", read));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(read).not.toHaveBeenCalled();
});

it("lists only authorized live patients and navigates to selected workspace", async () => {
  renderPage();
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
  );
  const link = await screen.findByRole("link", { name: /Lab patient/ });
  expect(link).toHaveAttribute("href", "/clinic/lab/photos/workspace-1");
  expect(screen.queryByLabelText("เลือกภาพสำหรับทดสอบ")).toBeNull();
  await userEvent.click(link);
  expect(await screen.findByLabelText("เลือกภาพสำหรับทดสอบ")).toBeEnabled();
});

it("validates the selected workspace against the live list before enabling save", async () => {
  renderPage("/clinic/lab/photos/workspace-1");
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
  );
  expect(await screen.findByLabelText("เลือกภาพสำหรับทดสอบ")).toBeEnabled();
  expect(screen.queryByText(/ภาพต้นฉบับของผู้ป่วย/)).toBeNull();
});

it("renders the selected workspace without duplicate React keys", async () => {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    renderPage("/clinic/lab/photos/pat-verify-01");
    server.use(
      http.get("*/api/live/synthetic-patients", () =>
        HttpResponse.json({
          items: [
            {
              workspace_patient_id: "pat-verify-01",
              native_patient_id: "native-1",
              display_name: "Lab patient",
            },
          ],
        }),
      ),
    );
    expect(await screen.findByLabelText("เลือกภาพสำหรับทดสอบ")).toBeEnabled();
    await screen.findByText("ไม่พบภาพที่บันทึกในหน้านี้");
    expect(
      consoleError.mock.calls.filter((args) =>
        args.some((arg) => typeof arg === "string" && arg.includes("same key")),
      ),
    ).toHaveLength(0);
  } finally {
    consoleError.mockRestore();
  }
});

it("fails closed for an unknown workspace or malformed mapping", async () => {
  renderPage("/clinic/lab/photos/other");
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByLabelText("เลือกภาพสำหรับทดสอบ")).toBeNull();
});

it("submits only the verified workspace key, never the native ID", async () => {
  vi.mocked(savePhoto).mockResolvedValueOnce({
    status: "pending",
    operation_id: "OP-1",
  });
  renderPage("/clinic/lab/photos/workspace-1");
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
  );
  const input = await screen.findByLabelText("เลือกภาพสำหรับทดสอบ");
  fireEvent.change(input, {
    target: { files: [new File(["image"], "test.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "บันทึกภาพทดสอบ" }));
  await waitFor(() =>
    expect(savePhoto).toHaveBeenCalledWith(
      "workspace-1",
      expect.any(String),
      expect.any(String),
    ),
  );
});

it("rejects duplicate workspace keys", async () => {
  renderPage("/clinic/lab/photos/workspace-1");
  const row = {
    workspace_patient_id: "workspace-1",
    native_patient_id: "native-1",
    display_name: "Lab patient",
  };
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({ items: [row, row] }),
    ),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByLabelText("เลือกภาพสำหรับทดสอบ")).toBeNull();
});

it("refreshes live photos only after server confirms save", async () => {
  vi.mocked(savePhoto).mockResolvedValueOnce({
    status: "pending",
    operation_id: "OP-1",
  });
  vi.mocked(photoStatus).mockResolvedValueOnce({
    status: "saved",
    operation_id: "OP-1",
  });
  renderPage("/clinic/lab/photos/workspace-1");
  const reads = vi.fn(() => HttpResponse.json({ items: [] }));
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
    http.get("*/api/live/synthetic-patients/workspace-1/photos", reads),
  );
  const input = await screen.findByLabelText("เลือกภาพสำหรับทดสอบ");
  await waitFor(() => expect(reads).toHaveBeenCalledTimes(1));
  fireEvent.change(input, {
    target: { files: [new File(["image"], "test.png", { type: "image/png" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "บันทึกภาพทดสอบ" }));
  await screen.findByText(/กำลังดำเนินการ/);
  expect(reads).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "ตรวจสอบสถานะ" }));
  await waitFor(() => expect(reads).toHaveBeenCalledTimes(2));
});

it.each(["DOC", "NUR"] as const)(
  "shows authorized photo metadata to %s without the native patient reference",
  async (role) => {
    renderPage(
      "/clinic/lab/photos/workspace-1",
      { "write.photo.upload": "enabled" },
      "live",
      role,
    );
    const content =
      "/api/live/synthetic-patients/workspace-1/photos/photo-1/content";
    server.use(
      http.get("*/api/live/synthetic-patients", () =>
        HttpResponse.json({
          items: [
            {
              workspace_patient_id: "workspace-1",
              native_patient_id: "native-1",
              display_name: "Lab patient",
            },
          ],
        }),
      ),
      http.get("*/api/live/synthetic-patients/workspace-1/photos", () =>
        HttpResponse.json({
          items: [
            {
              photo_id: "photo-1",
              patient_reference: "native-1",
              sha256: "a".repeat(64),
              width: 100,
              height: 200,
              created_at: "2026-01-01",
              content_url: content,
            },
          ],
        }),
      ),
    );
    expect(await screen.findByRole("img", { name: /photo-1/ })).toHaveAttribute(
      "src",
      content,
    );
    expect(screen.getByText("100 × 200 px")).toBeVisible();
    expect(screen.queryByText("native-1")).toBeNull();
    if (role === "NUR") {
      expect(screen.queryByLabelText("เลือกภาพสำหรับทดสอบ")).toBeNull();
      expect(screen.queryByRole("button", { name: "บันทึกภาพทดสอบ" })).toBeNull();
    }
  },
);

it("does not request live patients for other roles", async () => {
  const read = vi.fn(() => HttpResponse.json({ items: [] }));
  renderPage("/clinic/lab/photos", {}, "live", "REC");
  server.use(http.get("*/api/live/synthetic-patients", read));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(read).not.toHaveBeenCalled();
});

it("shows a read error instead of unverified photos", async () => {
  renderPage("/clinic/lab/photos/workspace-1");
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "workspace-1",
            native_patient_id: "native-1",
            display_name: "Lab patient",
          },
        ],
      }),
    ),
    http.get(
      "*/api/live/synthetic-patients/workspace-1/photos",
      () => new HttpResponse(null, { status: 403 }),
    ),
  );
  expect(await screen.findByText(/ไม่สามารถโหลดหรือตรวจสอบภาพ/)).toBeVisible();
  expect(screen.queryByRole("img")).toBeNull();
});
