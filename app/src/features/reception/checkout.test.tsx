import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import {
  renderWithFixtures,
  setViewport,
  withFixtures,
} from "../../test/render";
import { CheckoutPage } from "./CheckoutPage";

it("never shows clinical note content", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "withNote" });
  expect(await screen.findByText("Daniel Okafor")).toBeVisible();
  expect(screen.queryByText(/patient has rash|คนไข้มีผื่น/i)).toBeNull();
  const response = await fetch("/api/read/frontdesk-checkouts");
  expect(JSON.stringify(await response.json())).not.toContain(
    "patient has rash",
  );
});

it("filters source handoffs by payment state and preserves mobile table associations", async () => {
  setViewport(390);
  renderWithFixtures(<CheckoutPage />);
  expect(await screen.findByText("Daniel Okafor")).toBeVisible();
  await userEvent.selectOptions(
    screen.getByLabelText(/สถานะการชำระเงิน|payment state/i),
    "partial",
  );
  expect(await screen.findByText("INV-FIX-0006")).toBeVisible();
  expect(screen.queryByText("Daniel Okafor")).toBeNull();
  const table = screen.getByRole("table");
  const headers = within(table).getAllByRole("columnheader");
  const row = within(table).getByText("INV-FIX-0006").closest("tr");
  expect(row).not.toBeNull();
  expect(
    within(row as HTMLTableRowElement)
      .getAllByRole("cell")
      .map((cell) => cell.getAttribute("headers")),
  ).toEqual(headers.map((header) => header.id));
});

it("renders no invoice links even with billing capability enabled", async () => {
  renderWithFixtures(<CheckoutPage />, {
    visit: "invoiced",
    caps: { "read.billing": "enabled" },
  });
  expect(await screen.findByText("INV-FIX-0001")).toBeVisible();
  expect(screen.queryByRole("link", { name: /INV-FIX-0001/ })).toBeNull();
});
it("blocks handoff when the visit is incomplete", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "incomplete" });
  expect(
    await screen.findByText(/ยังตรวจไม่เสร็จ|visit not complete/i),
  ).toBeVisible();
  expect(screen.queryByRole("button", { name: /invoice|ใบแจ้งหนี้/i })).toBeNull();
  expect(screen.queryByText(/฿0\.00|THB 0\.00/)).toBeNull();
});
it("shows an invoice as noninteractive reference without verified destination", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "invoiced" });
  expect(await screen.findByText("INV-FIX-0001")).toBeVisible();
  expect(screen.queryByRole("link", { name: /INV-FIX-0001/ })).toBeNull();
  expect(screen.getByText(/ยังไม่ยืนยัน|not verified/i)).toBeVisible();
});
it("opens the existing reception booking flow from a ready handoff", async () => {
  renderWithFixtures(<CheckoutPage />, {
    visit: "invoiced",
    caps: { "write.appointment.book": "enabled" },
  });
  const link = await screen.findByRole("link", {
    name: /นัดติดตาม|follow-up/i,
  });
  const href = link.getAttribute("href");
  expect(href).not.toBeNull();
  const url = new URL(String(href), "https://clinic.test");
  expect(url.pathname).toBe("/clinic/reception");
  expect(url.searchParams.get("book")).toBe("1");
  expect(url.searchParams.get("patient")).toBe("fixture-dir-05");
  expect(url.searchParams.get("source")).toBe("checkout");
  expect(url.searchParams.get("visit")).toBe("fixture-visit-05");
});

it("disables follow-up while booking is not enabled", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "invoiced" });
  const link = await screen.findByRole("link", {
    name: /นัดติดตาม|follow-up/i,
  });
  expect(link).toHaveAttribute("aria-disabled", "true");
  expect(link).not.toHaveAttribute("href");
});

it("does not offer checkout follow-up booking to a cashier", async () => {
  renderWithFixtures(<CheckoutPage />, {
    role: "CAS",
    visit: "invoiced",
    caps: { "write.appointment.book": "enabled" },
  });
  const button = await screen.findByRole("button", {
    name: /นัดติดตาม|follow-up/i,
  });
  expect(button).toBeDisabled();
  expect(
    screen.getByText("ฝ่ายต้อนรับเป็นผู้สร้างนัดติดตาม"),
  ).toBeVisible();
});

it("renders authored missing-invoice and incomplete-visit sample states", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "invoiced" });
  expect(
    await screen.findByText("ตัวอย่างข้อมูลเช็คเอาท์ที่เขียนไว้"),
  ).toBeVisible();
  expect(screen.getByText("SAMPLE-VIS-CHECKOUT-01")).toBeVisible();
  expect(screen.getByText("SAMPLE-VIS-INCOMPLETE-01")).toBeVisible();
  expect(
    screen.getByText("การตรวจเสร็จแล้วแต่ยังไม่มีใบแจ้งหนี้"),
  ).toBeVisible();
  expect(
    screen.getByText("การตรวจยังไม่เสร็จตอนเช็คเอาท์"),
  ).toBeVisible();
  for (const gap of ["การประเมิน", "แผนการรักษา", "หัตถการ"]) {
    expect(screen.getByText(gap)).toBeVisible();
  }
});


it("paginates more than 20 checkout rows and resets to the first page after filtering", async () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({
    id: `checkout-${i}`,
    patientId: `patient-${i}`,
    patientName: `Test Patient ${i}`,
    visitId: `visit-${i}`,
    visitStatus: "complete",
    handoff: i < 5 ? "partial" : "invoiced",
    fictional: true,
  }));
  const app = withFixtures(<CheckoutPage />);
  server.use(
    http.get("/api/read/frontdesk-checkouts", ({ request }) => {
      const url = new URL(request.url);
      const filters = JSON.parse(url.searchParams.get("filters") ?? "{}");
      const selected = rows.filter(
        (row) => !filters.handoff || filters.handoff === row.handoff,
      );
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      return HttpResponse.json({
        items: selected.slice(offset, offset + limit),
        total: selected.length,
      });
    }),
  );
  render(app);
  expect(await screen.findByText("Test Patient 0")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /ถัดไป|next/i }));
  expect(await screen.findByText("Test Patient 20")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ก่อนหน้า|previous/i }),
  );
  expect(await screen.findByText("Test Patient 0")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /ถัดไป|next/i }));
  await screen.findByText("Test Patient 20");
  await userEvent.selectOptions(
    screen.getByLabelText(/สถานะการชำระเงิน|payment state/i),
    "partial",
  );
  expect(await screen.findByText("Test Patient 0")).toBeVisible();
  expect(screen.queryByText("Test Patient 20")).toBeNull();
  expect(screen.queryByRole("button", { name: /ถัดไป|next/i })).toBeNull();
});
