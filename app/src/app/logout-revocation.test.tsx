import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../test/msw/server";
import { clearNativeCsrfToken } from "../transport/native/session";
import { SessionProvider, useSession } from "./SessionProvider";

const SESSION = {
  sessionEpoch: "epoch-1",
  user: { id: "u1", displayName: "User", roles: ["REC"] },
  capabilities: {},
};

function Harness() {
  const { status, session, logout, refresh } = useSession();
  const client = useQueryClient();
  const [outcome, setOutcome] = useState("idle");
  const [tick, setTick] = useState(0);

  return (
    <>
      <output data-testid="status">
        {status}:{session?.user.id ?? "none"}
      </output>
      <output data-testid="cache" key={tick}>
        {client.getQueryData(["protected"]) === undefined
          ? "cleared"
          : "present"}
      </output>
      <output data-testid="outcome">{outcome}</output>
      <button
        type="button"
        onClick={() => {
          client.setQueryData(["protected"], { rows: ["private row"] });
          setTick((value) => value + 1);
        }}
      >
        Seed cache
      </button>
      <button
        type="button"
        onClick={() => {
          void logout().then(
            (result) => setOutcome(result.status),
            (cause: unknown) => {
              const code =
                typeof cause === "object" && cause !== null && "code" in cause
                  ? String(cause.code)
                  : "UNKNOWN";
              setOutcome(`rejected:${code}`);
            },
          );
        }}
      >
        Logout
      </button>
      <button type="button" onClick={() => void refresh()}>
        Refresh session
      </button>
    </>
  );
}

function renderHarness() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <Harness />
      </SessionProvider>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  clearNativeCsrfToken();
});

it("revokes protected identity and cached rows while the logout POST is pending", async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let loggedOut = false;
  let posts = 0;

  server.use(
    http.get("/api/session", () =>
      loggedOut
        ? new HttpResponse(null, { status: 401 })
        : HttpResponse.json(SESSION),
    ),
    http.post("/api/auth/logout", async () => {
      posts += 1;
      await held;
      loggedOut = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );

  renderHarness();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Seed cache" }));
  expect(screen.getByTestId("cache")).toHaveTextContent("present");

  fireEvent.click(screen.getByRole("button", { name: "Logout" }));

  // The local identity and cache are gone before the POST resolves.
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("loading:none"),
  );
  expect(screen.getByTestId("cache")).toHaveTextContent("cleared");
  expect(posts).toBe(1);

  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("anonymous:none"),
  );
  expect(screen.getByTestId("outcome")).toHaveTextContent("anonymous");
});

it("reconciles with GET after a failed logout and never repeats the POST", async () => {
  let posts = 0;
  let reads = 0;
  server.use(
    http.get("/api/session", () => {
      reads += 1;
      return HttpResponse.json(SESSION);
    }),
    http.post("/api/auth/logout", () => {
      posts += 1;
      return HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      );
    }),
  );

  renderHarness();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1"),
  );
  const readsBefore = reads;

  fireEvent.click(screen.getByRole("button", { name: "Logout" }));

  await waitFor(() =>
    expect(screen.getByTestId("outcome")).toHaveTextContent(
      "rejected:FORBIDDEN",
    ),
  );
  expect(posts).toBe(1);
  // The reconciliation GET is the only thing that restores protected state.
  expect(reads).toBe(readsBefore + 1);
  expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1");
  expect(screen.getByTestId("cache")).toHaveTextContent("cleared");
});

it("stays in the error state when the reconciliation GET fails", async () => {
  let loggedOutAttempt = false;
  server.use(
    http.get("/api/session", () =>
      loggedOutAttempt ? HttpResponse.error() : HttpResponse.json(SESSION),
    ),
    http.post("/api/auth/logout", () => {
      loggedOutAttempt = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );

  renderHarness();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Logout" }));

  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("error:none"),
  );
  expect(screen.getByTestId("cache")).toHaveTextContent("cleared");
  expect(screen.getByTestId("outcome")).toHaveTextContent("error");
});

it("keeps a genuinely new session confirmed during logout without a second POST", async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let posts = 0;
  let replaceSession = false;
  server.use(
    http.get("/api/session", () =>
      HttpResponse.json(
        replaceSession
          ? {
              sessionEpoch: "epoch-2",
              user: { id: "u2", displayName: "Other", roles: ["REC"] },
              capabilities: {},
            }
          : SESSION,
      ),
    ),
    http.post("/api/auth/logout", async () => {
      posts += 1;
      await held;
      return new HttpResponse(null, { status: 204 });
    }),
  );

  renderHarness();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Logout" }));
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("loading:none"),
  );

  // Another tab's login replaces the server session while logout is in flight.
  replaceSession = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u2"),
  );

  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(screen.getByTestId("outcome")).toHaveTextContent("superseded"),
  );
  expect(posts).toBe(1);
  expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u2");
});

it("reports a failed native logout without any POST when credentials are invalidated mid-flight", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  const nativeSession = {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "u1", displayName: "User", roles: ["REC"] },
    capabilities: {},
    csrfToken: "native-token-1",
  };
  const posts = vi.fn();
  server.use(
    http.get("/api/method/clinic_ui.api.session.bootstrap", () =>
      HttpResponse.json({ message: nativeSession }),
    ),
    http.post("/api/method/logout", () => {
      posts();
      return HttpResponse.json({ message: "Logged Out" });
    }),
  );

  renderHarness();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1"),
  );

  fireEvent.click(screen.getByRole("button", { name: "Logout" }));
  // Credentials are invalidated while nativeLogout holds its token snapshot.
  clearNativeCsrfToken();

  await waitFor(() =>
    expect(screen.getByTestId("outcome")).toHaveTextContent(
      "rejected:UPSTREAM_UNAVAILABLE",
    ),
  );
  // Reconciliation found the still-live session, so the failure is reported
  // instead of a silent success, and no logout POST was ever sent.
  expect(screen.getByTestId("status")).toHaveTextContent("authenticated:u1");
  expect(posts).not.toHaveBeenCalled();
});
