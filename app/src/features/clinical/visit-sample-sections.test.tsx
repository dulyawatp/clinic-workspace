import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { renderWithFixtures } from "../../test/render";
import { VisitWorkspacePage } from "./VisitWorkspacePage";

function renderSection(section: string) {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.visitPattern} element={<VisitWorkspacePage />} />
    </Routes>,
    {
      role: "DOC",
      path: `${ROUTES.visit("ENC-1")}?section=${section}`,
    },
  );
}

/**
 * The read-model encounter keeps its verified read-only sections. The three
 * designed sections that used to be placeholders now render their real layout
 * over sample rows, and read encounters carry none of those rows.
 */
it.each([
  ["lab", "ไม่มีคำขอแล็บสำหรับผู้ป่วยรายนี้ในข้อมูลตัวอย่าง"],
  ["procedures", "ไม่มีหัตถการสำหรับผู้ป่วยรายนี้ในข้อมูลตัวอย่าง"],
] as const)(
  "renders the designed %s section instead of a placeholder",
  async (section, empty) => {
    renderSection(section);
    expect(await screen.findByText(empty)).toBeVisible();
    expect(
      screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
    ).toBeNull();
  },
);

it("shows the unverified native lab entry on the read encounter", async () => {
  renderSection("lab");
  expect(
    await screen.findByRole("heading", { name: "ผลตรวจในคลินิก" }),
  ).toBeVisible();
  expect(screen.getByText("ยังไม่ยืนยัน", { selector: ".pc-badge" })).toBeVisible();
});

it("keeps the photos section as entry points to the photo surfaces", async () => {
  renderSection("photos");
  const galleryLinks = await screen.findAllByRole("link", {
    name: "ภาพถ่ายผู้ป่วย",
  });
  expect(
    galleryLinks.some(
      (link) => link.getAttribute("href") === ROUTES.photos("PAT-1"),
    ),
  ).toBe(true);
  expect(
    screen.getByRole("link", { name: "เปรียบเทียบและวาดกำกับ" }),
  ).toHaveAttribute("href", ROUTES.photoCompare("PAT-1"));
});
