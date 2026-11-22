import clinical from "@clinic/contracts/fixtures/clinical.json";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { compatible, readPatientPhotos } from "./model";
import { PhotoComparePage, PhotoGalleryPage } from "./PhotoPages";

vi.mock("react-konva", () => import("./annotate/konva-test-support"));

function renderPhoto(
  compare = false,
  options: Parameters<typeof renderWithFixtures>[1] = {},
) {
  const path = `/clinic/patients/PAT-1/photos${compare ? "/compare" : ""}`;
  return renderWithFixtures(
    <Routes>
      <Route
        path="/clinic/patients/:id/photos"
        element={<PhotoGalleryPage />}
      />
      <Route
        path="/clinic/patients/:id/photos/compare"
        element={<PhotoComparePage />}
      />
    </Routes>,
    { role: "DOC", path, ...options },
  );
}

it.each(["DOC", "NUR"] as const)(
  "renders fixture originals and consent guidance for %s",
  async (role) => {
    renderPhoto(false, { role });
    expect(await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/)).toBeVisible();
    expect(screen.getByText(/ขั้นตอนที่คลินิกอนุญาต/)).toBeVisible();
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getAllByRole("img")[0]).toHaveAttribute(
      "src",
      "/api/files/fixture-photo-1",
    );
    expect(
      screen.queryByRole("button", { name: /ถ่ายภาพ|capture|upload/i }),
    ).toBeNull();
  },
);

it("denies capability before any clinical reads", async () => {
  const seen: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    if (request.url.includes("/api/read/clinical-")) seen.push(request.url);
  };
  server.events.on("request:start", listener);
  try {
    renderPhoto(false, { caps: { "read.photo": "denied" } });
    expect(await screen.findByText(/ไม่มีสิทธิ์/)).toBeVisible();
    expect(seen).toEqual([]);
  } finally {
    server.events.removeListener("request:start", listener);
  }
});

it("surfaces server role denial as error instead of empty", async () => {
  renderPhoto(false, { role: "REC" });
  expect(await screen.findByText(/ไม่มีสิทธิ์/)).toBeVisible();
  expect(screen.queryByText(/ไม่พบภาพถ่าย/)).toBeNull();
});

it("rejects a wrong-patient response rather than rendering it", async () => {
  renderPhoto();
  server.use(
    http.get("*/api/read/clinical-photos", () =>
      HttpResponse.json({
        items: [{ ...clinical.photos[0], patientId: "PAT-OTHER" }],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("img")).toBeNull();
});

it("rejects wrong-patient identity before showing photos", async () => {
  renderPhoto();
  server.use(
    http.get("*/api/read/clinical-visits", () =>
      HttpResponse.json({
        items: [{ ...clinical.visits[0], patientId: "OTHER" }],
        total: 1,
      }),
    ),
  );
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByRole("img")).toBeNull();
});

it("shows empty and error states separately", async () => {
  renderPhoto();
  server.use(
    http.get("*/api/read/clinical-photos", () =>
      HttpResponse.json({ items: [], total: 0 }),
    ),
  );
  expect(await screen.findByText(/ไม่พบภาพถ่าย/)).toBeVisible();
});

it.each([null, "other"])(
  "keeps comparison unavailable for viewpoint %s",
  async (viewpoint) => {
    renderPhoto(true);
    server.use(
      http.get("*/api/read/clinical-photos", () =>
        HttpResponse.json({
          items: [clinical.photos[0], { ...clinical.photos[1], viewpoint }],
          total: 2,
        }),
      ),
    );
    expect(await screen.findByText(/ไม่สามารถเปรียบเทียบได้/)).toBeVisible();
    expect(screen.queryByTestId("compare-pair")).toBeNull();
  },
);

it("shows a failed original without substituting another image", async () => {
  renderPhoto();
  await screen.findByText(/ภาพต้นฉบับของผู้ป่วย/);
  const image = screen.getAllByRole("img")[0];
  if (!image) throw new Error("missing image");
  fireEvent.error(image);
  expect(screen.getByText(/ไม่สามารถเปิดภาพต้นฉบับ/)).toBeVisible();
  expect(screen.getAllByRole("img")).toHaveLength(1);
});

it("presents separate figures and a keyboard-operable reveal slider", async () => {
  renderPhoto(true);
  expect(await screen.findByText(/เปรียบเทียบเคียงข้าง/)).toBeVisible();
  expect(screen.getByText(/เปรียบเทียบแบบเลื่อน/)).toBeVisible();
  const range = screen
    .getAllByRole("slider", { name: /เลื่อนเปรียบเทียบ/ })
    .find((element) => element.tagName === "INPUT");
  if (!range) throw new Error("missing native range");
  fireEvent.change(range, { target: { value: "70" } });
  expect(range).toHaveValue("70");
  expect(screen.getByText("70%")).toBeVisible();
  expect(screen.getAllByRole("combobox")).toHaveLength(2);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "ภาพก่อน" }),
    "",
  );
  expect(screen.queryByTestId("compare-pair")).toBeNull();
});

it("disables comparison when original fails", async () => {
  renderPhoto(true);
  await screen.findByTestId("compare-pair");
  fireEvent.error(screen.getAllByRole("img")[0] as HTMLElement);
  expect(await screen.findAllByText(/ไม่สามารถเปิดภาพต้นฉบับ/)).not.toHaveLength(0);
  expect(
    screen.queryAllByRole("slider", { name: /เลื่อนเปรียบเทียบ/ }),
  ).toHaveLength(0);
});

it("does not compare unknown, incompatible or wrong-patient viewpoints", () => {
  const before = clinical.photos[0] as Parameters<typeof compatible>[0];
  const after = clinical.photos[1] as Parameters<typeof compatible>[1];
  expect(compatible(before, after)).toBe(true);
  expect(compatible({ ...before, viewpoint: null }, after)).toBe(false);
  expect(compatible(before, { ...after, viewpoint: "other" })).toBe(false);
  expect(compatible(before, { ...after, patientId: "OTHER" })).toBe(false);
  expect(compatible(before, { ...after, bodyArea: "arm" })).toBe(false);
});

it("reads beyond the first page and rejects incomplete pagination", async () => {
  const rows = Array.from({ length: 101 }, (_, index) => ({
    ...clinical.photos[0],
    id: `PH-${index}`,
  }));
  const calls = vi.fn(({ request }: { request: Request }) => {
    const offset = Number(new URL(request.url).searchParams.get("offset"));
    return HttpResponse.json({
      items: rows.slice(offset, offset + 100),
      total: rows.length,
    });
  });
  server.use(http.get("*/api/read/clinical-photos", calls));
  expect(
    await readPatientPhotos("PAT-1", new AbortController().signal),
  ).toHaveLength(101);
  expect(calls).toHaveBeenCalledTimes(2);
  server.use(
    http.get("*/api/read/clinical-photos", () =>
      HttpResponse.json({ items: [], total: 1 }),
    ),
  );
  await expect(
    readPatientPhotos("PAT-1", new AbortController().signal),
  ).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
});
