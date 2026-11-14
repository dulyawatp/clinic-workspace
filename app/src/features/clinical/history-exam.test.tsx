import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import {
  renderWithCaps,
  renderWithFixtures,
  setViewport,
} from "../../test/render";
import { ExamSection } from "./ExamSection";
import { VisitWorkspacePage } from "./VisitWorkspacePage";
import { VitalsTable } from "./VitalsTable";

function visit(
  section: string,
  options: {
    allergies?: "none" | "unknown";
    weight?: null;
    role?: "DOC" | "NUR";
    encounterId?: string;
  } = {},
) {
  return renderWithFixtures(
    <Routes>
      <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
    </Routes>,
    {
      role: "DOC",
      path: `/clinic/visits/ENC-1?section=${section}`,
      ...options,
    },
  );
}

it("marks prior history read-only without copy or editable fields", async () => {
  visit("history");
  expect(await screen.findByText("Example prior record")).toBeVisible();
  expect(screen.getByTestId("history-readonly")).toBeVisible();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: /copy|คัดลอก/i })).toBeNull();
});

it("renders vital values with their own units, time and source", async () => {
  visit("exam");
  expect(await screen.findByText("62 kg")).toBeVisible();
  expect(screen.getByText("98.6 °F")).toBeVisible();
  expect(screen.getAllByText("nurse")).toHaveLength(2);
  expect(screen.getAllByText(/2026-11-05T02:05:00Z/)).toHaveLength(2);
  expect(screen.getByText("Example examination")).toBeVisible();
});

it("shows missing vital without inventing zero", async () => {
  visit("exam", { weight: null });
  expect(await screen.findByText("98.6 °F")).toBeVisible();
  expect(screen.queryByText("0 kg")).toBeNull();
  expect(screen.getByText(/ยังไม่บันทึก|not recorded/i)).toBeVisible();
});

it("distinguishes unknown allergies from none", async () => {
  visit("overview", { allergies: "unknown" });
  expect(await screen.findByText(/ไม่ทราบ|unknown/i)).toBeVisible();
  expect(screen.queryByText(/ไม่มีประวัติแพ้|none reported/i)).toBeNull();
});

it("shows none only when reported", async () => {
  visit("overview", { allergies: "none" });
  expect(await screen.findByText(/ไม่มีประวัติแพ้|none reported/i)).toBeVisible();
});

it("shows missing data and never borrows another encounter", async () => {
  renderWithFixtures(
    <Routes>
      <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
    </Routes>,
    { role: "DOC", path: "/clinic/visits/ENC-2?section=history" },
  );
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
  expect(screen.queryByText("Example prior record")).toBeNull();
});

it("shows denied section reads without concealing permitted vitals", async () => {
  visit("exam", { role: "NUR" });
  expect(
    await screen.findByText(/ไม่มีสิทธิ์|does not have permission/i),
  ).toBeVisible();
  expect(await screen.findByText("62 kg")).toBeVisible();
  expect(screen.queryByText("Example examination")).toBeNull();
});

it("rejects section rows with mismatched identity", async () => {
  visit("history");
  server.use(
    http.get("/api/read/clinical-sections", () =>
      HttpResponse.json({
        items: [
          {
            encounterId: "ENC-2",
            patientId: "PAT-1",
            section: "history",
            past: "Wrong encounter",
          },
        ],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
  expect(screen.queryByText("Wrong encounter")).toBeNull();
});

it("drops previous history when navigating between mounted encounters", async () => {
  setViewport(1440);
  renderWithFixtures(
    <>
      <Link to="/clinic/visits/ENC-2?section=history">Next</Link>
      <Routes>
        <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
      </Routes>
    </>,
    { role: "DOC", path: "/clinic/visits/ENC-1?section=history" },
  );
  expect(await screen.findByText("Example prior record")).toBeVisible();
  await userEvent.click(screen.getByRole("link", { name: "Next" }));
  expect(screen.queryByText("Example prior record")).toBeNull();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-2");
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
});

it("renders English labels and preserves recorded zero", () => {
  renderWithCaps(
    {},
    <VitalsTable
      vitals={[
        {
          id: "v",
          encounterId: "ENC-1",
          patientId: "PAT-1",
          kind: "weight",
          value: 0,
          unit: "kg",
          measuredAt: "2026-11-05T02:05:00Z",
          source: "nurse",
        },
      ]}
    />,
    { locale: "en" },
  );
  expect(screen.getByText("Weight")).toBeVisible();
  expect(screen.getByText("0 kg")).toBeVisible();
  expect(screen.getByText("Measured at")).toBeVisible();
});

it("fails closed for mismatched vital rows", async () => {
  visit("exam");
  server.use(
    http.get("/api/read/clinical-vitals", () =>
      HttpResponse.json({
        items: [
          {
            id: "wrong",
            patientId: "PAT-1",
            encounterId: "ENC-2",
            kind: "weight",
            value: 999,
            unit: "kg",
            measuredAt: "2026-11-05",
            source: "nurse",
          },
        ],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
  expect(screen.queryByText("999 kg")).toBeNull();
});

it("shows a failed vital read without inventing values", async () => {
  visit("exam");
  server.use(
    http.get("/api/read/clinical-vitals", () =>
      HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
    ),
  );
  expect(
    await screen.findByText(/ไม่มีสิทธิ์|does not have permission/i),
  ).toBeVisible();
  expect(screen.queryByText("62 kg")).toBeNull();
});

it("paginates vitals past 20 and resets on encounter change", async () => {
  setViewport(1440);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests: Array<{
    encounterId: string;
    patientId: string;
    offset: number;
  }> = [];
  const records = Array.from({ length: 23 }, (_, i) => ({
    id: `v-${i}`,
    encounterId: "ENC-1",
    patientId: "PAT-1",
    kind: "weight",
    value: i + 1,
    unit: i === 20 ? "lb" : "kg",
    measuredAt: "2026-11-05T02:05:00Z",
    source: "nurse",
  }));
  renderWithFixtures(
    <>
      <Link to="/clinic/visits/ENC-2?section=exam">Next visit</Link>
      <Routes>
        <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
      </Routes>
    </>,
    { role: "DOC", path: "/clinic/visits/ENC-1?section=exam" },
  );
  server.use(
    http.get("/api/read/clinical-vitals", async ({ request }) => {
      const url = new URL(request.url);
      const filters = JSON.parse(url.searchParams.get("filters") ?? "{}");
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "20");
      requests.push({ ...filters, offset });
      if (offset === 20) await pending;
      const items =
        filters.encounterId === "ENC-1" && filters.patientId === "PAT-1"
          ? records.slice(offset, offset + limit)
          : [];
      return HttpResponse.json({
        items,
        total: filters.encounterId === "ENC-1" ? records.length : 0,
      });
    }),
  );
  expect(await screen.findByText("20 kg")).toBeVisible();
  expect(screen.queryByText("21 lb")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  await waitFor(() =>
    expect(requests).toContainEqual({
      encounterId: "ENC-1",
      patientId: "PAT-1",
      offset: 20,
    }),
  );
  expect(screen.queryByText("20 kg")).toBeNull();
  expect(screen.queryByText("21 lb")).toBeNull();
  release();
  expect(await screen.findByText("21 lb")).toBeVisible();
  expect(screen.getByText("23 kg")).toBeVisible();
  expect(screen.getAllByText("nurse")).toHaveLength(3);
  expect(screen.getAllByText("2026-11-05T02:05:00Z")).toHaveLength(3);
  expect(screen.queryByText("20 kg")).toBeNull();
  await userEvent.click(screen.getByRole("link", { name: "Next visit" }));
  expect(screen.queryByText("21 lb")).toBeNull();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-2");
  expect(await screen.findAllByText(/ไม่มีข้อมูล|no data/i)).not.toHaveLength(0);
  expect(requests).toContainEqual({
    encounterId: "ENC-2",
    patientId: "PAT-1",
    offset: 0,
  });
});

it("shows later-page errors without showing stale measurements", async () => {
  visit("exam");
  server.use(
    http.get("/api/read/clinical-vitals", ({ request }) => {
      const offset = Number(
        new URL(request.url).searchParams.get("offset") ?? "0",
      );
      if (offset)
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      return HttpResponse.json({
        items: [
          {
            id: "v",
            encounterId: "ENC-1",
            patientId: "PAT-1",
            kind: "weight",
            value: 62,
            unit: "kg",
            measuredAt: "2026-11-05",
            source: "nurse",
          },
        ],
        total: 21,
      });
    }),
  );
  expect(await screen.findByText("62 kg")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(
    await screen.findByText(/ไม่มีสิทธิ์|does not have permission/i),
  ).toBeVisible();
  expect(screen.queryByText("62 kg")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: /หน้าก่อนหน้า|previous page/i }),
  );
  expect(await screen.findByText("62 kg")).toBeVisible();
});

function SwitchPatient() {
  const [patientId, setPatientId] = useState("PAT-1");
  return (
    <>
      <button type="button" onClick={() => setPatientId("PAT-2")}>
        Change patient
      </button>
      <ExamSection patientId={patientId} encounterId="ENC-1" />
    </>
  );
}

it("resets pagination when the patient filter changes in a mounted exam", async () => {
  const requests: Array<{
    patientId: string;
    encounterId: string;
    offset: number;
  }> = [];
  renderWithFixtures(<SwitchPatient />, { role: "DOC" });
  server.use(
    http.get("/api/read/clinical-vitals", ({ request }) => {
      const url = new URL(request.url);
      const filters = JSON.parse(url.searchParams.get("filters") ?? "{}");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      requests.push({ ...filters, offset });
      const value =
        filters.patientId === "PAT-2" ? 77 : offset === 20 ? 55 : 44;
      return HttpResponse.json({
        items: [
          {
            id: "v",
            encounterId: filters.encounterId,
            patientId: filters.patientId,
            kind: "weight",
            value,
            unit: "kg",
            measuredAt: "2026-11-05",
            source: "nurse",
          },
        ],
        total: filters.patientId === "PAT-2" ? 1 : 21,
      });
    }),
  );
  expect(await screen.findByText("44 kg")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(await screen.findByText("55 kg")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Change patient" }));
  expect(screen.queryByText("55 kg")).toBeNull();
  expect(await screen.findByText("77 kg")).toBeVisible();
  expect(requests).toContainEqual({
    patientId: "PAT-2",
    encounterId: "ENC-1",
    offset: 0,
  });
});
