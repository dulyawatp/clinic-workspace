import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import {
  renderWithCaps,
  renderWithDataRouter,
  renderWithFixtures,
} from "../../test/render";
import { useEncounterDraft } from "./EncounterDraft";
import { ReviewDetails } from "./ReviewDetails";

beforeEach(() => {
  sessionStorage.clear();
  server.use(
    http.get("/api/live/encounter-drafts", () =>
      HttpResponse.json({ drafts: [], truncated: false }),
    ),
  );
});

it.each([
  ["pending", "en", /pending or unresolved.*Do not retry until reconciled/],
  ["unknown", "en", /pending or unresolved.*Do not retry until reconciled/],
  ["pending", "th", /รอดำเนินการหรือยังไม่ได้ข้อยุติ.*อย่าลองใหม่/],
  ["unknown", "th", /รอดำเนินการหรือยังไม่ได้ข้อยุติ.*อย่าลองใหม่/],
] as const)(
  "blocks fresh sessions for server %s (%s) across reads",
  async (state, locale, message) => {
    let reads = 0;
    let writes = 0;
    server.use(
      http.get("/api/live/encounter-review", () => {
        reads++;
        return HttpResponse.json({
          review_details: "Original",
          modified: "v1",
          write_state: state,
        });
      }),
      http.post("/api/write/encounter-review", () => {
        writes++;
        return HttpResponse.json({});
      }),
    );
    const dirty = vi.fn();
    renderWithCaps(
      {},
      <ReviewDetails
        identity="doc"
        requestKey="draft-key"
        onDirtyChange={dirty}
      />,
      { locale },
    );
    const user = userEvent.setup();
    const field = await screen.findByDisplayValue("Original");
    await screen.findByText(message);
    const save = screen.getByRole("button", {
      name: locale === "en" ? "Save Review Details" : "บันทึกรายละเอียดการตรวจ",
    });
    expect(save).toBeDisabled();
    await user.type(field, " edit");
    await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
    for (let i = 2; i <= 3; i++) {
      await user.click(
        screen.getByRole("button", {
          name: locale === "en" ? "Reload saved version" : "โหลดฉบับที่บันทึกใหม่",
        }),
      );
      await waitFor(() => expect(reads).toBe(i));
      await screen.findByText(message);
      expect(save).toBeDisabled();
      expect(field).toHaveValue("Original edit");
    }
    expect(writes).toBe(0);
  },
);

it("isolates server and local locks by identity and respects local locks on clear", async () => {
  sessionStorage.setItem(
    `encounter-review-lock:${JSON.stringify(["doc", "locked"])}`,
    "unknown",
  );
  server.use(
    http.get("/api/live/encounter-review", ({ request }) => {
      const key = new URL(request.url).searchParams.get("request_key");
      return HttpResponse.json({
        review_details: key,
        modified: "v1",
        write_state: key === "pending" ? "pending" : "clear",
      });
    }),
  );
  const view = renderWithCaps(
    {},
    <ReviewDetails identity="doc" requestKey="pending" />,
    { locale: "en" },
  );
  await screen.findByDisplayValue("pending");
  expect(
    screen.getByRole("button", { name: "Save Review Details" }),
  ).toBeDisabled();
  view.unmount();
  const clear = renderWithCaps(
    {},
    <ReviewDetails identity="doc" requestKey="clear" />,
    { locale: "en" },
  );
  await screen.findByDisplayValue("clear");
  expect(
    screen.getByRole("button", { name: "Save Review Details" }),
  ).toBeEnabled();
  clear.unmount();
  renderWithCaps({}, <ReviewDetails identity="doc" requestKey="locked" />, {
    locale: "en",
  });
  await screen.findByDisplayValue("locked");
  expect(
    screen.getByRole("button", { name: "Save Review Details" }),
  ).toBeDisabled();
});

it.each([
  ["th", "INTENT_PENDING", /มีการอัปเดตที่รอดำเนินการหรือยังไม่ได้ข้อยุติ/],
  ["en", "INTENT_PENDING", /An update is pending or unresolved/],
  ["th", "INTENT_UNCONFIRMED", /ผลการอัปเดตก่อนหน้ายังไม่ได้รับการยืนยัน/],
  ["en", "INTENT_UNCONFIRMED", /An earlier update has an unconfirmed outcome/],
] as const)("handles %s %s without retry", async (locale, code, message) => {
  let writes = 0;
  let reads = 0;
  server.use(
    http.get("/api/live/encounter-review", () => {
      reads++;
      return HttpResponse.json({ review_details: "Original", modified: "v1" });
    }),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", () => {
      writes++;
      return HttpResponse.json({ code }, { status: 409 });
    }),
  );
  const view = renderWithCaps(
    {},
    <ReviewDetails identity="doc" requestKey="draft-key" />,
    { locale },
  );
  const user = userEvent.setup();
  const field = await screen.findByDisplayValue("Original");
  await user.type(field, " edit");
  const save = screen.getByRole("button", {
    name: locale === "en" ? "Save Review Details" : "บันทึกรายละเอียดการตรวจ",
  });
  await user.click(save);
  await screen.findByText(message);
  expect(field).toHaveValue("Original edit");
  expect(save).toBeDisabled();
  const key = `encounter-review-lock:${JSON.stringify(["doc", "draft-key"])}`;
  expect(sessionStorage.getItem(key)).toBe(
    code === "INTENT_PENDING" ? null : "unconfirmed",
  );
  await user.click(
    screen.getByRole("button", {
      name: locale === "en" ? "Reload saved version" : "โหลดฉบับที่บันทึกใหม่",
    }),
  );
  await waitFor(() => expect(reads).toBe(2));
  expect(field).toHaveValue("Original edit");
  await waitFor(() => {
    if (code === "INTENT_UNCONFIRMED") expect(save).toBeDisabled();
    else expect(save).toBeEnabled();
  });
  expect(writes).toBe(1);
  if (code === "INTENT_UNCONFIRMED") {
    view.unmount();
    renderWithCaps(
      {},
      <ReviewDetails identity="doc" requestKey="draft-key" />,
      { locale },
    );
    await screen.findByText(message);
    expect(
      screen.getByRole("button", {
        name: locale === "en" ? "Save Review Details" : "บันทึกรายละเอียดการตรวจ",
      }),
    ).toBeDisabled();
  }
});

it("updates the dirty baseline on reload without replacing edits", async () => {
  let remote = "Original";
  server.use(
    http.get("/api/live/encounter-review", () =>
      HttpResponse.json({ review_details: remote, modified: "v1" }),
    ),
  );
  const dirty = vi.fn();
  renderWithCaps(
    {},
    <ReviewDetails
      identity="doc"
      requestKey="draft-key"
      onDirtyChange={dirty}
    />,
    { locale: "en" },
  );
  const user = userEvent.setup();
  const field = await screen.findByDisplayValue("Original");
  await user.type(field, " edit");
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
  remote = "Original edit";
  await user.click(
    screen.getByRole("button", { name: "Reload saved version" }),
  );
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false));
  expect(field).toHaveValue("Original edit");
  remote = "Different";
  await user.click(
    screen.getByRole("button", { name: "Reload saved version" }),
  );
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
  expect(field).toHaveValue("Original edit");
});

it("loads by draft key and manually saves the exact contract", async () => {
  let payload: unknown;
  server.use(
    http.get("/api/live/encounter-review", ({ request }) => {
      expect(new URL(request.url).searchParams.get("request_key")).toBe(
        "draft-key",
      );
      return HttpResponse.json({ review_details: "Original", modified: "v1" });
    }),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", async ({ request }) => {
      payload = await request.json();
      return HttpResponse.json({ status: "saved", modified: "v2" });
    }),
  );
  renderWithFixtures(<ReviewDetails identity="doc" requestKey="draft-key" />);
  const user = userEvent.setup();
  const field = await screen.findByRole("textbox");
  await screen.findByDisplayValue("Original");
  await user.clear(field);
  await user.type(field, "Edited");
  expect(payload).toBeUndefined();
  await user.click(screen.getByRole("button", { name: /บันทึกรายละเอียด/ }));
  await screen.findByText("บันทึกรายละเอียดการตรวจแล้ว");
  expect(payload).toEqual({
    request_key: "draft-key",
    review_details: "Edited",
    modified: "v1",
  });
  expect(JSON.stringify(sessionStorage)).not.toContain("Edited");
});

it("retains edits on stale and reloads the saved version for comparison", async () => {
  let reads = 0;
  server.use(
    http.get("/api/live/encounter-review", () =>
      HttpResponse.json(
        ++reads === 1
          ? { review_details: "Original", modified: "v1" }
          : { review_details: "Remote", modified: "v2" },
      ),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", () =>
      HttpResponse.json({ code: "CONFLICT_STALE" }, { status: 409 }),
    ),
  );
  renderWithFixtures(<ReviewDetails identity="doc" requestKey="draft-key" />);
  const user = userEvent.setup();
  const field = await screen.findByDisplayValue("Original");
  await user.clear(field);
  await user.type(field, "Mine");
  await user.click(screen.getByRole("button", { name: /บันทึกรายละเอียด/ }));
  await screen.findByText(/รายละเอียดถูกแก้ไขจากที่อื่น/);
  expect(field).toHaveValue("Mine");
  await user.click(screen.getByRole("button", { name: /โหลดฉบับ/ }));
  await screen.findByText("Remote");
  expect(field).toHaveValue("Mine");
});

it("locks uncertain writes across remount without storing text", async () => {
  let writes = 0;
  let reads = 0;
  server.use(
    http.get("/api/live/encounter-review", () => {
      reads++;
      return HttpResponse.json({ review_details: "Original", modified: "v1" });
    }),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", () => {
      writes++;
      return HttpResponse.json({ status: "unknown" });
    }),
  );
  const view = renderWithFixtures(
    <ReviewDetails identity="doc" requestKey="draft-key" />,
  );
  const user = userEvent.setup();
  const field = await screen.findByDisplayValue("Original");
  await user.type(field, " secret");
  await user.click(screen.getByRole("button", { name: /บันทึกรายละเอียด/ }));
  await screen.findByText(/ไม่ทราบผลการบันทึก/);
  expect(field).toHaveValue("Original secret");
  expect(reads).toBe(1);
  expect(JSON.stringify(sessionStorage)).not.toContain("secret");
  view.unmount();
  renderWithFixtures(<ReviewDetails identity="doc" requestKey="draft-key" />);
  await screen.findByText(/ไม่ทราบผลการบันทึก/);
  expect(screen.getByRole("button", { name: /บันทึกรายละเอียด/ })).toBeDisabled();
  expect(writes).toBe(1);
});

it.each([
  [409, "UNSAFE_ENCOUNTER"],
  [403, "WRITE_DISABLED"],
  [404, "NOT_FOUND"],
  [404, "UNSUPPORTED"],
] as const)("handles %s %s as a definite refusal", async (status, code) => {
  server.use(
    http.get("/api/live/encounter-review", () =>
      HttpResponse.json({ review_details: "Original", modified: "v1" }),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", () =>
      HttpResponse.json({ code }, { status }),
    ),
  );
  renderWithFixtures(<ReviewDetails identity="doc" requestKey="draft-key" />);
  const user = userEvent.setup();
  const field = await screen.findByDisplayValue("Original");
  await user.type(field, " edit");
  await user.click(screen.getByRole("button", { name: /บันทึกรายละเอียด/ }));
  await screen.findByText(/ไม่สามารถอัปเดตร่างนี้ได้/);
  expect(field).toHaveValue("Original edit");
  expect(
    sessionStorage.getItem(
      `encounter-review-lock:${JSON.stringify(["doc", "draft-key"])}`,
    ),
  ).toBeNull();
});

it.each([429, 500, 502])("keeps HTTP %s ambiguous", async (status) => {
  server.use(
    http.get("/api/live/encounter-review", () =>
      HttpResponse.json({ review_details: "Original", modified: "v1" }),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-review", () =>
      HttpResponse.json({ code: "UPSTREAM_UNAVAILABLE" }, { status }),
    ),
  );
  renderWithFixtures(<ReviewDetails identity="doc" requestKey="draft-key" />);
  const user = userEvent.setup();
  await screen.findByDisplayValue("Original");
  await user.click(screen.getByRole("button", { name: /บันทึกรายละเอียด/ }));
  await screen.findByText(/ไม่ทราบผลการบันทึก/);
  expect(screen.getByRole("button", { name: /บันทึกรายละเอียด/ })).toBeDisabled();
});

function GuardHarness() {
  const draft = useEncounterDraft("doc");
  return (
    <>
      <button
        type="button"
        onClick={() =>
          draft.select({ display_name: "First", workspace_patient_id: "first" })
        }
      >
        First
      </button>
      <button
        type="button"
        onClick={() =>
          draft.select({
            display_name: "Second",
            workspace_patient_id: "second",
          })
        }
      >
        Second
      </button>
      <Link to="/other">Navigate</Link>
      {draft.panel}
    </>
  );
}

it("guards close, patient switch, navigation and browser unload while dirty", async () => {
  sessionStorage.setItem(
    `encounter-draft:${JSON.stringify(["doc", "first"])}`,
    JSON.stringify({ key: "draft-key", state: "created" }),
  );
  const { router } = renderWithDataRouter(
    [
      { path: "/", element: <GuardHarness /> },
      { path: "/other", element: <p>Other page</p> },
    ],
    { initialEntries: ["/"] },
  );
  server.use(
    http.get("/api/live/encounter-review", () =>
      HttpResponse.json({ review_details: "Original", modified: "v1" }),
    ),
  );
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  try {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "First" }));
    const field = await screen.findByDisplayValue("Original");
    await user.type(field, " edit");
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    await user.click(screen.getByRole("button", { name: "ปิด" }));
    await user.click(screen.getByRole("button", { name: "Second" }));
    await user.click(screen.getByRole("link", { name: "Navigate" }));
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(field).toHaveValue("Original edit");
    expect(router.state.location.pathname).toBe("/");
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("link", { name: "Navigate" }));
    await screen.findByText("Other page");
  } finally {
    confirm.mockRestore();
  }
});
