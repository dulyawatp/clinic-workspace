import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link, Route, Routes } from "react-router-dom";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../test/render";

import { VisitWorkspacePage } from "./VisitWorkspacePage";

function renderVisit(
  section: string,
  options: {
    role?: "DOC" | "NUR";
    state?: string;
    savedAt?: null;
    diagnoses?: unknown[];
    missing?: string[];
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

it("shows draft status, source fields and unverified composition without save", async () => {
  renderVisit("note", { savedAt: null });
  expect(await screen.findByText(/ฉบับร่าง|draft/i)).toBeVisible();
  expect(screen.getByText("Example assessment")).toBeVisible();
  expect(screen.getByText("Example plan")).toBeVisible();
  expect(
    screen.getByText(/ยังไม่ยืนยันการประกอบ|composition.*not verified/i),
  ).toBeVisible();
  expect(screen.queryByText(/บันทึกล่าสุด|last saved/i)).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: /บันทึก|save/i })).toBeNull();
});

it("shows server-reported signed state without claiming composed note", async () => {
  renderVisit("note", { state: "signed" });
  expect(await screen.findByText(/ลงนาม|signed/i)).toBeVisible();
  expect(
    screen.getByText(/ยังไม่ยืนยันการประกอบ|composition.*not verified/i),
  ).toBeVisible();
  expect(screen.getByText(/2026-11-05T02:30:00Z/)).toBeVisible();
});

it.each(["other-state", "constructor"])(
  "shows unrecognized note state %s raw, not as signed",
  async (state) => {
    renderVisit("note", { state });
    expect(await screen.findByText(new RegExp(state))).toBeVisible();
    expect(screen.queryByText(/ลงนาม|signed/i)).toBeNull();
  },
);

it("resets diagnosis page on encounter change", async () => {
  renderWithFixtures(
    <>
      <Link to="/clinic/visits/ENC-2?section=diagnoses">Next encounter</Link>
      <Routes>
        <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
      </Routes>
    </>,
    { role: "DOC", path: "/clinic/visits/ENC-1?section=diagnoses" },
  );
  const requests: Array<{
    encounterId: string;
    patientId: string;
    offset: number;
  }> = [];
  server.use(
    http.get("/api/read/clinical-diagnoses", ({ request }) => {
      const url = new URL(request.url);
      const filters = JSON.parse(url.searchParams.get("filters") ?? "{}");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      requests.push({ ...filters, offset });
      const rows =
        filters.encounterId === "ENC-1"
          ? Array.from({ length: 21 }, (_, i) => ({
              id: `d${i}`,
              code: `DX-${i}`,
              description: "example",
              encounterId: "ENC-1",
              patientId: "PAT-1",
            }))
          : [];
      return HttpResponse.json({
        items: rows.slice(offset, offset + 20),
        total: rows.length,
      });
    }),
  );
  expect(await screen.findByText("DX-19")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(await screen.findByText("DX-20")).toBeVisible();
  await userEvent.click(screen.getByRole("link", { name: "Next encounter" }));
  expect(screen.queryByText("DX-20")).toBeNull();
  expect(
    await screen.findByText(/ไม่พบรายการวินิจฉัย|no coded diagnoses returned/i),
  ).toBeVisible();
  expect(requests).toContainEqual({
    patientId: "PAT-1",
    encounterId: "ENC-2",
    offset: 0,
  });
});

it("renders empty diagnosis results without claiming coding happened", async () => {
  renderVisit("diagnoses", { diagnoses: [] });
  expect(
    await screen.findByText(/ไม่พบรายการวินิจฉัย|no coded diagnoses returned/i),
  ).toBeVisible();
});

it("shows only actual medication order status, not dispensed", async () => {
  renderVisit("medications");
  expect(await screen.findByText("Example medication")).toBeVisible();
  expect(screen.getByText(/สั่งยา|ordered/i)).toBeVisible();
  expect(screen.queryByText(/จ่ายยาแล้ว|dispensed/i)).toBeNull();
});

it("lists server missing sections without submit", async () => {
  renderVisit("review", { missing: ["exam", "note"] });
  expect(await screen.findByText(/ส่วนที่ยังขาด|missing sections/i)).toBeVisible();
  expect(
    screen.getByText(/การตรวจร่างกาย|examination/i, { selector: "li" }),
  ).toBeVisible();
  expect(screen.queryByRole("button", { name: /ยืนยัน|submit/i })).toBeNull();
});

it.each(["note", "diagnoses", "medications", "review"])(
  "does not show %s rows for denied nurse reads",
  async (section) => {
    renderVisit(section, { role: "NUR" });
    expect(
      await screen.findByText(/ไม่มีสิทธิ์|does not have permission/i),
    ).toBeVisible();
    expect(screen.queryByText("Example assessment")).toBeNull();
  },
);

it("clears clinical note when switching encounters", async () => {
  setViewport(1440);
  renderWithFixtures(
    <>
      <Link to="/clinic/visits/ENC-2?section=note">Next encounter</Link>
      <Routes>
        <Route path="/clinic/visits/:id" element={<VisitWorkspacePage />} />
      </Routes>
    </>,
    { role: "DOC", path: "/clinic/visits/ENC-1?section=note" },
  );
  expect(await screen.findByText("Example assessment")).toBeVisible();
  await userEvent.click(screen.getByRole("link", { name: "Next encounter" }));
  expect(screen.queryByText("Example assessment")).toBeNull();
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-2");
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
});

it("makes later diagnosis pages accessible", async () => {
  renderVisit("diagnoses");
  server.use(
    http.get("/api/read/clinical-diagnoses", ({ request }) => {
      const url = new URL(request.url);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const rows = Array.from({ length: 22 }, (_, i) => ({
        id: `D${i}`,
        code: `CODE-${i + 1}`,
        description: `Example ${i + 1}`,
        encounterId: "ENC-1",
        patientId: "PAT-1",
      }));
      return HttpResponse.json({
        items: rows.slice(offset, offset + 20),
        total: rows.length,
      });
    }),
  );
  expect(await screen.findByText("CODE-20")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(await screen.findByText("CODE-22")).toBeVisible();
  expect(screen.queryByText("CODE-20")).toBeNull();
});

it("rejects mismatched diagnosis rows", async () => {
  renderVisit("diagnoses");
  server.use(
    http.get("/api/read/clinical-diagnoses", () =>
      HttpResponse.json({
        items: [
          {
            id: "wrong",
            patientId: "PAT-1",
            encounterId: "ENC-2",
            code: "WRONG-CODE",
            description: "Wrong patient context",
          },
        ],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
  expect(screen.queryByText("WRONG-CODE")).toBeNull();
});

it("allows note access on later section pages", async () => {
  renderVisit("note");
  server.use(
    http.get("/api/read/clinical-sections", ({ request }) => {
      const url = new URL(request.url);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const rows = Array.from({ length: 20 }, () => ({
        encounterId: "ENC-1",
        patientId: "PAT-1",
        section: "history",
      }));
      rows.push({
        encounterId: "ENC-1",
        patientId: "PAT-1",
        section: "note",
        assessment: "Later assessment",
      } as (typeof rows)[number]);
      return HttpResponse.json({
        items: rows.slice(offset, offset + 20),
        total: rows.length,
      });
    }),
  );
  expect(await screen.findByText(/ไม่มีข้อมูล|no data/i)).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(await screen.findByText("Later assessment")).toBeVisible();
  expect(
    screen.getByText(/ยังไม่ยืนยันการประกอบ|composition.*not verified/i),
  ).toBeVisible();
});

it("shows unknown medication status raw without claiming ordered", async () => {
  renderVisit("medications");
  server.use(
    http.get("/api/read/clinical-medications", () =>
      HttpResponse.json({
        items: [
          {
            id: "m",
            encounterId: "ENC-1",
            patientId: "PAT-1",
            name: "Example medicine",
            state: "pending_external",
          },
        ],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByText(/pending_external/)).toBeVisible();
  expect(screen.queryByText(/สั่งยา|ordered/i)).toBeNull();
});

it("shows medication page errors without stale rows", async () => {
  renderVisit("medications");
  server.use(
    http.get("/api/read/clinical-medications", ({ request }) => {
      const offset = Number(
        new URL(request.url).searchParams.get("offset") ?? 0,
      );
      return offset
        ? HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 })
        : HttpResponse.json({
            items: [
              {
                id: "m",
                encounterId: "ENC-1",
                patientId: "PAT-1",
                name: "First page medicine",
                state: "ordered",
              },
            ],
            total: 21,
          });
    }),
  );
  expect(await screen.findByText("First page medicine")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: /ถัดไป|next page/i }),
  );
  expect(
    await screen.findByText(/ไม่มีสิทธิ์|does not have permission/i),
  ).toBeVisible();
  expect(screen.queryByText("First page medicine")).toBeNull();
});

it("does not infer completeness when the review field is missing", async () => {
  renderVisit("review");
  server.use(
    http.get("/api/read/clinical-sections", () =>
      HttpResponse.json({
        items: [
          { encounterId: "ENC-1", patientId: "PAT-1", section: "review" },
        ],
        total: 1,
      }),
    ),
  );
  expect(
    await screen.findByText(/ไม่มีข้อมูลจากแหล่งนี้|not available from this source/i),
  ).toBeVisible();
  expect(
    screen.queryByText(/ไม่มีส่วนที่ขาด|no missing sections reported/i),
  ).toBeNull();
});

it("does not send write requests during section navigation", async () => {
  let writes = 0;
  renderVisit("note");
  server.use(
    http.all("/api/*", ({ request }) => {
      if (["GET", "HEAD"].includes(request.method)) return;
      writes++;
      return HttpResponse.json({}, { status: 500 });
    }),
  );
  await screen.findByText("Example assessment");
  await userEvent.click(
    screen.getByRole("tab", { name: /การวินิจฉัย|diagnoses/i }),
  );
  await screen.findByText("Z00.0");
  await userEvent.click(
    screen.getByRole("tab", { name: /^06ยา|medications/i }),
  );
  await screen.findByText("Example medication");
  expect(writes).toBe(0);
});
