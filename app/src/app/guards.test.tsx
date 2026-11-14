import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHandlers } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { renderWithFixtures } from "../test/render";
import { App } from "./App";
import { RequireSession } from "./guards";
import {
  consumeReturnTo,
  RETURN_TO_KEY,
  rememberReturnTo,
  sanitizeReturnTo,
} from "./return-to";
import { ROUTES } from "./routes";

beforeEach(() => sessionStorage.removeItem(RETURN_TO_KEY));
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.removeItem(RETURN_TO_KEY);
});

async function completeReauth() {
  const user = userEvent.setup();
  await waitFor(() =>
    expect(screen.getByLabelText(/ชื่อผู้ใช้|username/i)).toBeEnabled(),
  );
  await user.type(await screen.findByLabelText(/ชื่อผู้ใช้|username/i), "rec");
  await user.type(screen.getByLabelText(/รหัสผ่าน|password/i), "fixture");
  await user.click(
    screen.getByRole("button", { name: /continue|ดำเนินการต่อ/i }),
  );
  await waitFor(() =>
    expect(screen.queryByLabelText(/ชื่อผู้ใช้|username/i)).toBeNull(),
  );
}

describe("presentation guards", () => {
  it("waits for session without exposing children or writing a target", () => {
    renderWithFixtures(
      <RequireSession>
        <p>Protected content</p>
      </RequireSession>,
      { authed: false },
    );
    expect(screen.getByRole("status")).toBeVisible();
    expect(screen.queryByText("Protected content")).toBeNull();
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  });

  it("renders authenticated children without writing return target", async () => {
    renderWithFixtures(
      <RequireSession>
        <p>Protected content</p>
      </RequireSession>,
      { role: "REC" },
    );
    expect(await screen.findByText("Protected content")).toBeVisible();
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  });

  it("stores only safe target and redirects anonymous user without target in login URL", async () => {
    window.history.pushState(
      {},
      "",
      `${ROUTES.home}?q=สมชาย&phone=0812345678&status=waiting#patient-123`,
    );
    server.use(...createHandlers({ authed: false }));
    render(<App />);
    await screen.findByLabelText("ชื่อผู้ใช้");
    expect(window.location.pathname + window.location.search).toBe(
      `${ROUTES.login}?reason=expired`,
    );
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(
      `${ROUTES.home}?status=waiting`,
    );
  });

  it("restores canonical home and safe query after real re-auth, consumes target once", async () => {
    window.history.pushState({}, "", `${ROUTES.home}?status=waiting`);
    server.use(...createHandlers({ authed: false }));
    render(<App />);
    await screen.findByLabelText("ชื่อผู้ใช้");
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    await completeReauth();
    expect(window.location.pathname + window.location.search).toBe(
      `${ROUTES.home}?status=waiting`,
    );
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
    expect(document.documentElement.lang).toBe("en");
  });

  it("never writes a dynamic record pathname or identifying query to storage", async () => {
    const writes = vi.spyOn(Storage.prototype, "setItem");
    renderWithFixtures(
      <RequireSession>
        <p>Patient</p>
      </RequireSession>,
      {
        authed: false,
        path: "/clinic/patients/PAT-12345?name=สมชาย&phone=0812345678&status=PAT-12345",
      },
    );
    await waitFor(() =>
      expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.home),
    );
    expect(writes).toHaveBeenCalledWith(RETURN_TO_KEY, ROUTES.home);
    for (const [, value] of writes.mock.calls)
      expect(value).not.toMatch(/PAT-12345|สมชาย|0812345678/);
    expect(screen.queryByText("Patient")).toBeNull();
  });

  it("does not consume re-auth target on invalid login", async () => {
    rememberReturnTo(`${ROUTES.home}?status=waiting`);
    window.history.pushState({}, "", ROUTES.login);
    server.use(...createHandlers({ authed: false }));
    render(<App />);
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("ชื่อผู้ใช้")).toBeEnabled());
    await user.type(screen.getByLabelText("ชื่อผู้ใช้"), "rec");
    await user.type(screen.getByLabelText("รหัสผ่าน"), "nope");
    await user.click(screen.getByRole("button", { name: "ดำเนินการต่อ" }));
    await screen.findByRole("alert");
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(
      `${ROUTES.home}?status=waiting`,
    );
  });

  it("revalidates tampered stored dynamic target before sign-in navigation", async () => {
    sessionStorage.setItem(
      RETURN_TO_KEY,
      "/clinic/patients/private-id?view=day",
    );
    window.history.pushState({}, "", ROUTES.login);
    server.use(...createHandlers({ authed: false }));
    render(<App />);
    await completeReauth();
    expect(window.location.pathname).toBe(ROUTES.home);
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  });
});

describe("return-target privacy boundary", () => {
  it("accepts canonical home and removes identifying parameters", () => {
    expect(
      sanitizeReturnTo(`${ROUTES.home}?q=สมชาย&status=waiting&id=PAT-1`),
    ).toBe(`${ROUTES.home}?status=waiting`);
  });

  it("accepts the static reception surface", () => {
    expect(sanitizeReturnTo(ROUTES.reception)).toBe(ROUTES.reception);
    expect(sanitizeReturnTo(`${ROUTES.reception}?scope=waiting`)).toBe(
      `${ROUTES.reception}?scope=waiting`,
    );
  });

  it("allows the static queue route without retaining patient searches", () => {
    expect(sanitizeReturnTo(`${ROUTES.queue}?q=patient-name`)).toBe(
      ROUTES.queue,
    );
    expect(
      sanitizeReturnTo(`${ROUTES.queue}?status=in_consultation&q=patient`),
    ).toBe(`${ROUTES.queue}?status=in_consultation`);
  });

  it("allows the static checkout route without retaining patient identifiers", () => {
    expect(
      sanitizeReturnTo(`${ROUTES.checkout}?patientId=fixture-dir-01`),
    ).toBe(ROUTES.checkout);
  });

  it.each([
    "/clinic/patients/123",
    "/clinic/visits/ENC-1",
    "/clinic/photos/photo-id",
    "/clinic/สมชาย",
    "https://evil.example",
    "//evil.example",
    ROUTES.login,
  ])("rejects non-allowlisted record or redirect target %s", (raw) => {
    expect(sanitizeReturnTo(raw)).toBeNull();
    rememberReturnTo(raw);
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(ROUTES.home);
  });

  it("rejects identifying values hidden in otherwise allowlisted keys", () => {
    expect(
      sanitizeReturnTo(
        `${ROUTES.profile}?status=สมชาย&view=0812345678&section=PAT-1&page=0812345678&range=ENC-2`,
      ),
    ).toBe(ROUTES.profile);
  });

  it("ignores prototype-like and duplicate query keys while retaining safe view metadata", () => {
    expect(
      sanitizeReturnTo(
        `${ROUTES.home}?constructor=private&status=waiting&status=Private`,
      ),
    ).toBe(`${ROUTES.home}?status=waiting`);
  });

  it("consumes sanitized storage once, dropping invalid/stale targets", () => {
    rememberReturnTo(`${ROUTES.profile}?view=day&name=Private`);
    expect(consumeReturnTo()).toBe(`${ROUTES.profile}?view=day`);
    expect(consumeReturnTo()).toBeNull();
    sessionStorage.setItem(RETURN_TO_KEY, "https://evil.example");
    expect(consumeReturnTo()).toBeNull();
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBeNull();
  });

  it("degrades safely when browser storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    expect(() => rememberReturnTo(ROUTES.home)).not.toThrow();
    expect(consumeReturnTo()).toBeNull();
  });
});
