import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AppointmentPage } from "./AppointmentPage";

const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";
const VIEW = "/api/method/clinic_ui.api.appointments.view";
const ALIAS = "appointment-alias-1";
const PATIENT = "WS-1";

const DETAIL = {
  workspace_patient_id: PATIENT,
  appointment_id: ALIAS,
  appointment_date: "2026-11-20",
  appointment_time: "09:30:00",
  status: "Open",
  modified: "2026-11-20 09:00:00.000000",
};

function serveLive(caps: Record<string, string> = {}) {
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
          capabilities: { "read.appointment": "read_only", ...caps },
          csrfToken: "csrf",
        },
      }),
    ),
    http.get(VIEW, () =>
      HttpResponse.json({
        message: { ...DETAIL, can_reschedule: false, can_checkin: false },
      }),
    ),
  );
}

function renderDetail() {
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: `/clinic/appointments/${ALIAS}` },
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("keeps cancel and no-show refusal coverage when the capability is not enabled", async () => {
  serveLive();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const user = userEvent.setup();
  renderDetail();

  const cancel = await screen.findByRole("button", { name: "ยกเลิกนัด" });
  const noShow = screen.getByRole("button", { name: "ระบุว่าไม่มา" });
  expect(cancel).toBeDisabled();
  expect(noShow).toBeDisabled();
  expect(screen.getAllByText(/ยังไม่เปิดใช้งาน|read-only/i).length).toBeGreaterThan(
    0,
  );
  await user.click(cancel);
  await user.click(noShow);

  expect(
    fetchSpy.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
  expect(
    fetchSpy.mock.calls.some(([url]) => String(url).includes(".cancel")),
  ).toBe(false);
  expect(
    fetchSpy.mock.calls.some(([url]) => String(url).includes("no_show")),
  ).toBe(false);
});

it("opens the existing reception booking flow with a return-visit source", async () => {
  serveLive({ "write.appointment.book": "enabled" });
  renderDetail();

  const link = await screen.findByRole("link", { name: "สร้างนัดติดตาม" });
  const href = link.getAttribute("href");
  expect(href).not.toBeNull();
  const url = new URL(String(href), "https://clinic.test");
  expect(url.pathname).toBe("/clinic/reception");
  expect(url.searchParams.get("book")).toBe("1");
  expect(url.searchParams.get("patient")).toBe(PATIENT);
  expect(url.searchParams.get("appointment")).toBe(ALIAS);
  expect(url.searchParams.get("source")).toBe("return");
});

it("carries the authored return-visit source when the record matches", async () => {
  serveLive({ "write.appointment.book": "enabled" });
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({
        message: {
          ...DETAIL,
          workspace_patient_id: "P-10241",
          appointment_id: "DC-APT-3301",
          can_reschedule: false,
          can_checkin: false,
        },
      }),
    ),
  );
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/appointments/:id" element={<AppointmentPage />} />
    </Routes>,
    { path: "/clinic/appointments/DC-APT-3301" },
  );

  const link = await screen.findByRole("link", { name: "สร้างนัดติดตาม" });
  const url = new URL(String(link.getAttribute("href")), "https://clinic.test");
  expect(url.searchParams.get("returnSource")).toBe("SAMPLE-RETURN-01");
  expect(url.searchParams.get("lastVisit")).toBe("DC-VIS-2041");
  expect(url.searchParams.get("suggestedType")).toBe("AT-02");
});

it("keeps return-visit disabled while booking is not enabled", async () => {
  serveLive();
  renderDetail();
  const link = await screen.findByRole("link", { name: "สร้างนัดติดตาม" });
  expect(link).toHaveAttribute("aria-disabled", "true");
  expect(link).not.toHaveAttribute("href");
});
