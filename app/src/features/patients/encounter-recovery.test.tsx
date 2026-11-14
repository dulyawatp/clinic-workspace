import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { beforeEach, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { useEncounterDraft } from "./EncounterDraft";

function Harness() {
  const [identity, setIdentity] = useState("doc-a");
  const draft = useEncounterDraft(identity);
  return (
    <>
      <button
        type="button"
        onClick={() =>
          draft.select({
            display_name: "Example",
            workspace_patient_id: "ref-1",
          })
        }
      >
        Select
      </button>
      <button type="button" onClick={() => setIdentity("doc-b")}>
        Switch identity
      </button>
      {draft.panel}
    </>
  );
}

beforeEach(() => sessionStorage.clear());

it("does not offer creation while checking drafts", async () => {
  server.use(
    http.get("/api/live/encounter-drafts", async () => {
      await new Promise(() => {});
      return HttpResponse.json({ drafts: [], truncated: false });
    }),
  );
  renderWithFixtures(<Harness />);
  await userEvent.setup().click(screen.getByText("Select"));
  expect(screen.getByText(/กำลังตรวจสอบร่างที่มีอยู่/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
});

it("opens each matching draft without exposing identifiers or storing review text", async () => {
  const reads: string[] = [];
  server.use(
    http.get("/api/live/encounter-drafts", () =>
      HttpResponse.json({
        drafts: [
          {
            request_key: "key-a",
            patient_reference: "ref-1",
            created_at: "2026-01-01",
          },
          {
            request_key: "key-b",
            patient_reference: "ref-1",
            created_at: "2026-01-02",
          },
          {
            request_key: "key-other",
            patient_reference: "other",
            created_at: "2026-01-03",
          },
        ],
        truncated: false,
      }),
    ),
    http.get("/api/live/encounter-review", ({ request }) => {
      const key = new URL(request.url).searchParams.get("request_key") ?? "";
      reads.push(key);
      return HttpResponse.json({
        review_details: "Private text",
        modified: "v1",
      });
    }),
  );
  renderWithFixtures(<Harness />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Select"));
  const buttons = await screen.findAllByRole("button", { name: "เปิดร่าง" });
  expect(buttons).toHaveLength(2);
  if (!buttons[0] || !buttons[1]) throw new Error("Missing draft buttons");
  await user.click(buttons[0]);
  await screen.findByDisplayValue("Private text");
  await user.click(buttons[1]);
  await waitFor(() => expect(reads).toEqual(["key-a", "key-b"]));
  expect(document.body.textContent).not.toContain("key-a");
  expect(JSON.stringify(sessionStorage)).not.toContain("Private text");
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
});

it("blocks creation on error and partial lists, with retry", async () => {
  let count = 0;
  server.use(
    http.get("/api/live/encounter-drafts", () =>
      ++count === 1
        ? new HttpResponse(null, { status: 500 })
        : HttpResponse.json({ drafts: [], truncated: true }),
    ),
  );
  renderWithFixtures(<Harness />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Select"));
  expect(await screen.findByText(/ตรวจสอบร่างที่มีอยู่ไม่ได้/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "ลองโหลดรายการร่างอีกครั้ง" }),
  );
  expect(await screen.findByText(/แสดงร่างเพียงบางส่วน/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
});

it("keeps unknown local lock authoritative and clears selection on identity change", async () => {
  sessionStorage.setItem(
    `encounter-draft:${JSON.stringify(["doc-a", "ref-1"])}`,
    JSON.stringify({ key: "pending", state: "unknown" }),
  );
  server.use(
    http.get("/api/live/encounter-drafts", () =>
      HttpResponse.json({
        drafts: [
          {
            request_key: "key-a",
            patient_reference: "ref-1",
            created_at: "2026-01-01",
          },
        ],
        truncated: false,
      }),
    ),
  );
  renderWithFixtures(<Harness />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Select"));
  expect(await screen.findByRole("button", { name: "เปิดร่าง" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
  await user.click(screen.getByText("Switch identity"));
  expect(screen.queryByRole("button", { name: "เปิดร่าง" })).toBeNull();
});
