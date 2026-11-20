import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { ReceptionHomePage } from "./ReceptionHomePage";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const LIST = "/api/method/clinic_ui.api.appointments.synthetic_appointments";
const LINK = "/api/method/frappe.desk.search.search_link";

const ROW = {
  workspace_patient_id: "WS-1",
  appointment_id: "appointment-alias-1",
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Open",
};

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({
        message: {
          mode: "live",
          sessionEpoch: "native-epoch-1",
          user: {
            id: "native|u1",
            displayName: "Native Receptionist",
            roles: ["REC"],
          },
          capabilities: {
            "read.appointment": "read_only",
            "write.appointment.book": "enabled",
          },
          csrfToken: "csrf",
        },
      }),
    ),
    http.get(LIST, () => HttpResponse.json({ message: { items: [ROW] } })),
    http.get(LINK, () => HttpResponse.json({ message: [] })),
    http.post(LINK, () => HttpResponse.json({ message: [{ value: "WS-1" }] })),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
});

it("claims the Demo/Sample source only when the authored return record is carried", async () => {
  renderWithFixtures(<ReceptionHomePage />, {
    path: "/clinic/reception?book=1&patient=WS-1&source=return&returnSource=SAMPLE-RETURN-01",
  });

  expect(
    await screen.findByText("แหล่งที่มาเป็นนัดติดตาม: ระเบียนตัวอย่าง Demo/Sample"),
  ).toBeVisible();
  expect(await screen.findByRole("option", { name: "WS-1" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "ผู้ป่วย" })).toHaveValue("");
  expect(screen.getByText(/ลิงก์นี้ไม่สามารถเลือกผู้ป่วยให้โดยอัตโนมัติ/)).toBeVisible();
});

it("opens a plain return-visit link without any Demo/Sample claim", async () => {
  renderWithFixtures(<ReceptionHomePage />, {
    path: "/clinic/reception?book=1&patient=WS-1&source=return",
  });

  expect(await screen.findByRole("combobox", { name: "ผู้ป่วย" })).toHaveValue("");
  expect(
    screen.queryByText("แหล่งที่มาเป็นนัดติดตาม: ระเบียนตัวอย่าง Demo/Sample"),
  ).toBeNull();
});

it("opens checkout follow-up without guessing a Patient link from context", async () => {
  renderWithFixtures(<ReceptionHomePage />, {
    path: "/clinic/reception?book=1&patient=WS-1&source=checkout&visit=checkout-alias",
  });
  expect(await screen.findByText("เปิดจากการนัดติดตามหลังปิดรายการ")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "ผู้ป่วย" })).toHaveValue("");
  expect(screen.queryByDisplayValue("checkout-alias")).not.toBeInTheDocument();
});
