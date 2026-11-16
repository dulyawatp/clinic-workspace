import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import type { RoleCode } from "../../domain/capability";
import { renderWithFixtures } from "../../test/render";
import { PhotoGalleryPage } from "./PhotoPages";

const PATH = "/clinic/patients/PAT-1/photos";

function renderGallery(
  role: RoleCode,
  sessionMode: "live" | "fixture" = "fixture",
) {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/patients/:id/photos" element={<PhotoGalleryPage />} />
    </Routes>,
    { role, sessionMode, path: PATH },
  );
}

it.each(["DOC", "NUR"] as const)(
  "reaches the synthetic lab-photo workspace from the %s photo surface in live mode",
  async (role) => {
    renderGallery(role, "live");
    expect(
      await screen.findByRole("link", { name: "ภาพข้อมูลจำลองในห้องทดลอง" }),
    ).toHaveAttribute("href", ROUTES.livePhotosPatient("PAT-1"));
  },
);

it.each(["DOC", "NUR"] as const)(
  "does not offer the live lab workspace to %s outside live mode",
  async (role) => {
    renderGallery(role);
    await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/);
    expect(
      screen.queryByRole("link", { name: "ภาพข้อมูลจำลองในห้องทดลอง" }),
    ).toBeNull();
  },
);

it("does not offer the clinician lab workspace to reception", async () => {
  renderGallery("REC", "live");
  // Reception never reads clinical photo records, so the surface itself denies.
  await screen.findByText(/ไม่มีสิทธิ์เข้าถึงหน้านี้/);
  expect(
    screen.queryByRole("link", { name: "ภาพข้อมูลจำลองในห้องทดลอง" }),
  ).toBeNull();
});
