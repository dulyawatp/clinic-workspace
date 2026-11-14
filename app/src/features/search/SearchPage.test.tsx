import { useQueryClient } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { RETURN_TO_KEY } from "../../app/return-to";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { createHandlers } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithFixtures, setViewport } from "../../test/render";
import { LanguageToggle } from "../shell/LanguageToggle";
import { SearchPage } from "./SearchPage";

const row = {
  id: "fixture-search",
  hn: "FX-SEARCH",
  fullName: "สมชาย Smith",
  phone: "08-FIXTURE-SEARCH",
  lastVisit: "2026-10-28",
};
function Controls() {
  const client = useQueryClient();
  const { refresh, status } = useSession();
  return (
    <>
      <output data-testid="session-status">{status}</output>
      <button
        type="button"
        onClick={() => void client.refetchQueries({ queryKey: ["patients"] })}
      >
        Refetch rows
      </button>
      <button type="button" onClick={() => void refresh()}>
        Refresh session
      </button>
    </>
  );
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  sessionStorage.removeItem(RETURN_TO_KEY);
});

it.each(["changed", "403"])(
  "cached A→B→A hides rows and totals until held confirmation (%s)",
  async (outcome) => {
    renderWithFixtures(<SearchPage />, { role: "REC", path: ROUTES.search });
    await screen.findByRole("table");
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let aReads = 0;
    let heldStarted = false;
    server.use(
      http.get("/api/read/patients", async ({ request }) => {
        const q = new URL(request.url).searchParams.get("q");
        if (q === "A") {
          if (++aReads === 1)
            return HttpResponse.json({
              items: [{ ...row, fullName: "Old A patient" }],
              total: 60,
            });
          heldStarted = true;
          await held;
          return outcome === "403"
            ? new HttpResponse(null, { status: 403 })
            : HttpResponse.json({
                items: [{ ...row, fullName: "Fresh A patient" }],
                total: 1,
              });
        }
        return HttpResponse.json({
          items: [{ ...row, fullName: "B patient" }],
          total: 1,
        });
      }),
    );
    const input = screen.getByRole("searchbox");
    fireEvent.change(input, { target: { value: "A" } });
    await screen.findByText("Old A patient");
    expect(screen.getByText("หน้า 1 จาก 3")).toBeVisible();
    fireEvent.change(input, { target: { value: "B" } });
    await screen.findByText("B patient");
    fireEvent.change(input, { target: { value: "A" } });
    await waitFor(() => expect(heldStarted).toBe(true));
    expect(input).toBeEnabled();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("Old A patient")).toBeNull();
    expect(screen.queryByText("B patient")).toBeNull();
    expect(screen.queryByText("หน้า 1 จาก 3")).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "หน้าผลการค้นหา" }),
    ).toBeNull();
    await act(async () => {
      release();
    });
    if (outcome === "403") {
      await screen.findByRole("alert");
      expect(screen.queryByRole("table")).toBeNull();
    } else {
      expect(await screen.findByText("Fresh A patient")).toBeVisible();
      expect(screen.getByText("หน้า 1 จาก 1")).toBeVisible();
    }
    expect(screen.queryByText("Old A patient")).toBeNull();
    expect(screen.queryByText("หน้า 1 จาก 3")).toBeNull();
  },
);

it("cached page return hides old rows/totals and keeps previous-page cancellation usable", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC", path: ROUTES.search });
  await screen.findByRole("table");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let pageTwoReads = 0;
  let heldSignal: AbortSignal | undefined;
  server.use(
    http.get("/api/read/patients", async ({ request }) => {
      const offset = new URL(request.url).searchParams.get("offset");
      if (offset === "20") {
        if (++pageTwoReads === 1)
          return HttpResponse.json({
            items: [{ ...row, fullName: "Old page two patient" }],
            total: 60,
          });
        heldSignal = request.signal;
        await held;
        return HttpResponse.json({
          items: [{ ...row, fullName: "Late page two patient" }],
          total: 40,
        });
      }
      return HttpResponse.json({
        items: [{ ...row, fullName: "Page one patient" }],
        total: 60,
      });
    }),
  );
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "page" },
  });
  await screen.findByText("Page one patient");
  fireEvent.click(screen.getByRole("button", { name: "ถัดไป" }));
  await screen.findByText("Old page two patient");
  fireEvent.click(screen.getByRole("button", { name: "ก่อนหน้า" }));
  await screen.findByText("Page one patient");
  fireEvent.click(screen.getByRole("button", { name: "ถัดไป" }));
  await waitFor(() => expect(heldSignal).toBeDefined());
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText("Old page two patient")).toBeNull();
  expect(screen.queryByText("หน้า 2 จาก 3")).toBeNull();
  expect(screen.queryByRole("navigation", { name: "หน้าผลการค้นหา" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "ก่อนหน้า" }));
  await waitFor(() => expect(heldSignal?.aborted).toBe(true));
  await screen.findByText("Page one patient");
  await act(async () => {
    release();
  });
  expect(screen.queryByText("Late page two patient")).toBeNull();
});

it("searches mixed Thai/English unchanged after 300ms, without address-bar or storage persistence", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC", path: ROUTES.search });
  await screen.findByRole("table");
  const input = screen.getByRole("searchbox");
  const requests = vi.spyOn(globalThis, "fetch");
  const storage = vi.spyOn(Storage.prototype, "setItem");
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fireEvent.change(input, { target: { value: "สมชาย Smith" } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(299);
  });
  expect(requests).not.toHaveBeenCalled();
  expect(screen.queryByRole("table")).toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(requests).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  vi.useRealTimers();
  expect(
    await screen.findByRole("cell", { name: "สมชาย Smith" }),
  ).toBeVisible();
  const read = requests.mock.calls.find(([url]) =>
    String(url).startsWith("/api/read/patients"),
  );
  expect(
    new URL(String(read?.[0]), window.location.origin).searchParams.get("q"),
  ).toBe("สมชาย Smith");
  expect(
    window.location.pathname + window.location.search + window.location.hash,
  ).toBe(ROUTES.search);
  expect(storage).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
});

it("localizes empty state and keeps query through language change; clear returns focus", async () => {
  renderWithFixtures(
    <>
      <LanguageToggle />
      <SearchPage />
    </>,
    { role: "REC", path: ROUTES.search },
  );
  const user = userEvent.setup();
  const input = await screen.findByRole("searchbox");
  await user.type(input, "zzzz");
  expect(
    await screen.findByRole("heading", { name: "ไม่พบผู้ป่วยที่ตรงกับคำค้น" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "EN" }));
  expect(
    screen.getByRole("heading", { name: "No patient matched that search" }),
  ).toBeVisible();
  expect(input).toHaveValue("zzzz");
  await user.click(screen.getByRole("button", { name: "Clear search" }));
  expect(input).toHaveValue("");
  expect(input).toHaveFocus();
  expect(await screen.findByRole("table")).toBeVisible();
});

it("paginates source at 20 rows, resets page on text edit and keeps page out of URL", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC", path: ROUTES.search });
  const requests = vi.spyOn(globalThis, "fetch");
  const input = await screen.findByRole("searchbox");
  fireEvent.change(input, { target: { value: "08" } });
  await screen.findByText("หน้า 1 จาก 2");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(
    21,
  );
  fireEvent.click(screen.getByRole("button", { name: "ถัดไป" }));
  await screen.findByText("หน้า 2 จาก 2");
  expect(screen.getByRole("region", { name: "ผลลัพธ์" })).toHaveFocus();
  expect(
    requests.mock.calls.some(([url]) => {
      const params = new URL(String(url), window.location.origin).searchParams;
      return (
        params.get("q") === "08" &&
        params.get("limit") === "20" &&
        params.get("offset") === "20"
      );
    }),
  ).toBe(true);
  fireEvent.change(input, { target: { value: "Smith" } });
  await screen.findByText("หน้า 1 จาก 1");
  expect(screen.getByRole("cell", { name: "สมชาย Smith" })).toBeVisible();
  expect(screen.getByRole("button", { name: "ก่อนหน้า" })).toBeDisabled();
  expect(window.location.search).toBe("");
});

it("aborts old search immediately on edit and never shows late old rows", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await screen.findByRole("table");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let oldSignal: AbortSignal | undefined;
  server.use(
    http.get("/api/read/patients", async ({ request }) => {
      const q = new URL(request.url).searchParams.get("q");
      if (q === "old") {
        oldSignal = request.signal;
        await held;
        return HttpResponse.json({
          items: [{ ...row, fullName: "Old private row" }],
          total: 1,
        });
      }
      return HttpResponse.json({
        items: [{ ...row, fullName: "New row" }],
        total: 1,
      });
    }),
  );
  const input = screen.getByRole("searchbox");
  fireEvent.change(input, { target: { value: "old" } });
  await waitFor(() => expect(oldSignal).toBeDefined());
  fireEvent.change(input, { target: { value: "new" } });
  await waitFor(() => expect(oldSignal?.aborted).toBe(true));
  await screen.findByText("New row");
  await act(async () => {
    release();
  });
  expect(screen.queryByText("Old private row")).toBeNull();
});

it("aborts pending page when returning to previous page", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await screen.findByRole("table");
  let pageSignal: AbortSignal | undefined;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("/api/read/patients", async ({ request }) => {
      if (new URL(request.url).searchParams.get("offset") === "20") {
        pageSignal = request.signal;
        await held;
      }
      return HttpResponse.json({ items: [row], total: 40 });
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "ถัดไป" }));
  await waitFor(() => expect(pageSignal).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "ก่อนหน้า" }));
  await waitFor(() => expect(pageSignal?.aborted).toBe(true));
  await act(async () => {
    release();
  });
  expect(await screen.findByText("หน้า 1 จาก 2")).toBeVisible();
});

it.each([401, 403, 503])(
  "read %s replaces cached results without fabricated rows",
  async (status) => {
    renderWithFixtures(
      <>
        <Controls />
        <SearchPage />
      </>,
      { role: "REC" },
    );
    await screen.findByRole("cell", { name: "สมชาย Smith" });
    server.use(
      http.get("/api/read/patients", () =>
        HttpResponse.json({ message: "private traceback" }, { status }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Refetch rows" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("สมชาย Smith")).toBeNull();
    expect(screen.queryByText("private traceback")).toBeNull();
    expect(screen.queryByText("หน้า 1 จาก 2")).toBeNull();
  },
);

it("session uncertainty hides rows and resets component query before verified new session", async () => {
  renderWithFixtures(
    <>
      <Controls />
      <SearchPage />
    </>,
    { role: "REC" },
  );
  const input = await screen.findByRole("searchbox");
  fireEvent.change(input, { target: { value: "Smith" } });
  await screen.findByRole("cell", { name: "สมชาย Smith" });
  server.use(http.get("/api/session", () => HttpResponse.error()));
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(screen.queryByRole("table")).toBeNull();
  await screen.findByRole("alert");
  expect(screen.queryByRole("searchbox")).toBeNull();
  server.use(
    http.get("/api/session", () =>
      HttpResponse.json({
        sessionEpoch: "new-epoch",
        user: { id: "new-user", displayName: "New user", roles: ["REC"] },
        capabilities: {},
      }),
    ),
    http.get("/api/read/patients", () =>
      HttpResponse.json({ items: [], total: 0 }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "ตรวจสอบเซสชันอีกครั้ง" }));
  expect(await screen.findByRole("searchbox")).toHaveValue("");
  expect(screen.queryByText("สมชาย Smith")).toBeNull();
});

it.each(["denied", "blocked_unverified"] as const)(
  "%s capability never starts patient read or renders search controls",
  async (state) => {
    renderWithFixtures(<SearchPage />, {
      role: "REC",
      caps: { "read.patient": state },
    });
    const reads = vi.fn();
    server.use(
      http.get("/api/read/patients", () => {
        reads();
        return HttpResponse.json({ items: [row], total: 1 });
      }),
    );
    await screen.findByRole("heading", { name: "ไม่มีสิทธิ์เข้าถึงหน้านี้" });
    expect(reads).not.toHaveBeenCalled();
    expect(screen.queryByRole("searchbox")).toBeNull();
  },
);

it("confirmed expiry retains only static return route, never search text", async () => {
  renderWithFixtures(
    <>
      <Controls />
      <Routes>
        <Route path={ROUTES.search} element={<SearchPage />} />
        <Route path={ROUTES.login} element={<p>Sign in required</p>} />
      </Routes>
    </>,
    { role: "REC", path: ROUTES.search },
  );
  const input = await screen.findByRole("searchbox");
  fireEvent.change(input, { target: { value: "สมชาย" } });
  await screen.findByRole("cell", { name: "สมชาย Smith" });
  server.use(
    http.get("/api/session", () => new HttpResponse(null, { status: 401 })),
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  await screen.findByText("Sign in required");
  expect(screen.queryByRole("table")).toBeNull();
  expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.search);
});

it("composition waits for completed text before debounced GET", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await screen.findByRole("table");
  const input = screen.getByRole("searchbox");
  const requests = vi.spyOn(globalThis, "fetch");
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "สม" } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(400);
  });
  expect(requests).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(299);
  });
  expect(requests).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(requests).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  vi.useRealTimers();
  expect(
    await screen.findByRole("cell", { name: "สมชาย Smith" }),
  ).toBeVisible();
});

it("fixture source denial is an error, not a client role-based read grant", async () => {
  renderWithFixtures(<SearchPage />, { role: "PHA" });
  await screen.findByRole("alert");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText("สมชาย Smith")).toBeNull();
});

it.each([390, 834, 1440] as const)(
  "real protected search route exposes labelled summary table at %s",
  async (width) => {
    setViewport(width);
    server.use(...createHandlers({ role: "REC" }));
    window.history.pushState({}, "", ROUTES.search);
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "ค้นหาผู้ป่วย" }),
    ).toBeVisible();
    expect(screen.getByRole("searchbox")).toHaveAccessibleName("ค้นหาผู้ป่วย");
    const table = await screen.findByRole("table", { name: "ผลลัพธ์" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(4);
    expect(screen.queryByText(/internal_notes|Fixture-only note/)).toBeNull();
    expect(within(table).queryByRole("link")).toBeNull();
  },
);
