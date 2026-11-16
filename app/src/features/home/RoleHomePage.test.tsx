import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it } from "vitest";
import { renderWithFixtures } from "../../test/render";
import { RoleHomePage } from "./RoleHomePage";

beforeEach(() => {
  localStorage.clear();
});

it.each(["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"] as const)(
  "renders the %s role home with shortcuts and a Demo/Sample work queue",
  async (role) => {
    renderWithFixtures(<RoleHomePage />, { role });
    expect(await screen.findByTestId("role-home")).toBeVisible();
    const work = screen.getByTestId("home-work-items");
    expect(
      within(work).getByTestId("sample-banner"),
    ).toBeVisible();
    const shortcuts = screen.getByRole("region", { name: "ทางลัด" });
    expect(within(shortcuts).getAllByRole("link").length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(within(work).getAllByRole("listitem").length).toBeGreaterThan(0),
    );
  },
);

it("marks a work item read and keeps the state across a remount for that user", async () => {
  const user = userEvent.setup();
  const first = renderWithFixtures(<RoleHomePage />, { role: "REC" });
  const work = await screen.findByTestId("home-work-items");
  expect(await within(work).findByText("ยังไม่อ่าน: 2")).toBeVisible();

  await user.click(await within(work).findByRole("link", { name: /รอเรียกเก็บ/ }));
  expect(await within(work).findByText("ยังไม่อ่าน: 1")).toBeVisible();
  first.unmount();

  renderWithFixtures(<RoleHomePage />, { role: "REC" });
  const remounted = await screen.findByTestId("home-work-items");
  expect(within(remounted).getByText("ยังไม่อ่าน: 1")).toBeVisible();
});

it("dismisses a work item and keeps it hidden on remount", async () => {
  const user = userEvent.setup();
  const first = renderWithFixtures(<RoleHomePage />, { role: "REC" });
  const work = await screen.findByTestId("home-work-items");
  await user.click(
    await within(work).findByRole("button", { name: "ซ่อน · รอเรียกเก็บ" }),
  );
  await waitFor(() => expect(within(work).queryByText("รอเรียกเก็บ")).toBeNull());
  first.unmount();

  renderWithFixtures(<RoleHomePage />, { role: "REC" });
  const remounted = await screen.findByTestId("home-work-items");
  expect(within(remounted).queryByText("รอเรียกเก็บ")).toBeNull();
  expect(within(remounted).getByText("งานของฉัน")).toBeVisible();
});

it("keeps read and dismissed state scoped to the signed-in user", async () => {
  const user = userEvent.setup();
  const reception = renderWithFixtures(<RoleHomePage />, { role: "REC" });
  const work = await screen.findByTestId("home-work-items");
  await user.click(
    await within(work).findByRole("button", { name: "ซ่อน · รอเรียกเก็บ" }),
  );
  reception.unmount();

  renderWithFixtures(<RoleHomePage />, { role: "DOC" });
  const doctorWork = await screen.findByTestId("home-work-items");
  expect(within(doctorWork).getByText("นัดหมาย")).toBeVisible();
  expect(within(doctorWork).queryByText("รอเรียกเก็บ")).toBeNull();
});

it("shows the designed empty state when every work item is dismissed", async () => {
  const user = userEvent.setup();
  const result = renderWithFixtures(<RoleHomePage />, { role: "PHA" });
  const work = await screen.findByTestId("home-work-items");
  await user.click(
    await within(work).findByRole("button", { name: "ซ่อน · สต็อกต่ำ" }),
  );
  expect(await within(work).findByText("ไม่มีงานค้างสำหรับบทบาทนี้")).toBeVisible();
  result.unmount();
});
