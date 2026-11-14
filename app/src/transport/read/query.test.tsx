import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { AppProviders } from "../../app/providers";
import { useSession } from "../../app/SessionProvider";
import type { RoleCode } from "../../domain/capability";
import type { SessionDTO } from "../../domain/session";
import { server } from "../../test/msw/server";
import { useReadQuery } from "./query";

interface ScenarioState {
  role: RoleCode;
  authed: boolean;
}

function sessionFor(role: RoleCode): SessionDTO {
  return {
    sessionEpoch: "test-session",
    user: {
      id: `fixture-${role.toLowerCase()}`,
      displayName: `Fixture ${role}`,
      roles: [role],
    },
    capabilities: {},
  };
}

function sessionHandler(state: ScenarioState) {
  return http.get("/api/session", () =>
    state.authed
      ? HttpResponse.json(sessionFor(state.role))
      : HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 }),
  );
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function ReadProbe() {
  const { status, refresh } = useSession();
  const query = useReadQuery<{ id: string }>("patients", {});

  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="rows">
        {query.data === undefined
          ? "none"
          : query.data.items.map((item) => item.id).join(",")}
      </span>
      <span data-testid="error">{query.error?.code ?? "none"}</span>
      <button
        type="button"
        onClick={() => {
          void refresh();
        }}
      >
        refresh session
      </button>
    </div>
  );
}

it("clears protected read cache during session uncertainty and refetches only after recovery", async () => {
  const state: ScenarioState = { role: "DOC", authed: true };
  let reads = 0;
  server.use(
    sessionHandler(state),
    http.get("/api/read/patients", () => {
      reads += 1;
      return HttpResponse.json({ items: [{ id: `row-${reads}` }], total: 1 });
    }),
  );
  render(
    <AppProviders>
      <ReadProbe />
    </AppProviders>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("rows")).toHaveTextContent("row-1"),
  );
  server.use(
    http.get("/api/session", () => new HttpResponse(null, { status: 503 })),
  );
  fireEvent.click(screen.getByRole("button", { name: "refresh session" }));
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("error"),
  );
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
  expect(reads).toBe(1);
  server.use(sessionHandler(state));
  fireEvent.click(screen.getByRole("button", { name: "refresh session" }));
  await waitFor(() =>
    expect(screen.getByTestId("rows")).toHaveTextContent("row-2"),
  );
  expect(reads).toBe(2);
});

it("drops the previous user's cached rows on an in-place session change", async () => {
  const state: ScenarioState = { role: "DOC", authed: true };
  server.use(
    sessionHandler(state),
    http.get("/api/read/patients", () =>
      state.role === "PHA"
        ? HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 })
        : HttpResponse.json({ items: [{ id: `row-${state.role}` }], total: 1 }),
    ),
  );

  render(
    <AppProviders>
      <ReadProbe />
    </AppProviders>,
  );

  await waitFor(() =>
    expect(screen.getByTestId("rows")).toHaveTextContent("row-DOC"),
  );

  state.role = "PHA";
  fireEvent.click(screen.getByRole("button", { name: "refresh session" }));

  // The refetch under the new identity is denied: an error state, not the
  // previous user's rows.
  await waitFor(() =>
    expect(screen.getByTestId("error")).toHaveTextContent("FORBIDDEN"),
  );
  expect(screen.getByTestId("status")).toHaveTextContent("authenticated");
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
  expect(screen.queryByText("row-DOC")).toBeNull();
});

it("clears cached rows and stops reading after logout", async () => {
  const state: ScenarioState = { role: "DOC", authed: true };
  let readCalls = 0;
  server.use(
    sessionHandler(state),
    http.get("/api/read/patients", () => {
      readCalls += 1;
      return HttpResponse.json({ items: [{ id: "row-DOC" }], total: 1 });
    }),
  );

  render(
    <AppProviders>
      <ReadProbe />
    </AppProviders>,
  );

  await waitFor(() =>
    expect(screen.getByTestId("rows")).toHaveTextContent("row-DOC"),
  );
  expect(readCalls).toBe(1);

  state.authed = false;
  fireEvent.click(screen.getByRole("button", { name: "refresh session" }));

  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("anonymous"),
  );
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
  expect(screen.queryByText("row-DOC")).toBeNull();

  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(readCalls).toBe(1);
});

it("never shows an in-flight response from the previous session", async () => {
  const state: ScenarioState = { role: "DOC", authed: true };
  const gate = deferred();
  let readCalls = 0;
  server.use(
    sessionHandler(state),
    http.get("/api/read/patients", async () => {
      readCalls += 1;
      if (state.role === "DOC") {
        await gate.promise;
        return HttpResponse.json({ items: [{ id: "row-DOC" }], total: 1 });
      }
      return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
    }),
  );

  render(
    <AppProviders>
      <ReadProbe />
    </AppProviders>,
  );

  await waitFor(() => expect(readCalls).toBe(1));
  expect(screen.getByTestId("status")).toHaveTextContent("authenticated");
  expect(screen.getByTestId("rows")).toHaveTextContent("none");

  state.role = "PHA";
  fireEvent.click(screen.getByRole("button", { name: "refresh session" }));
  await waitFor(() =>
    expect(screen.getByTestId("error")).toHaveTextContent("FORBIDDEN"),
  );

  // The late DOC response must not be shown under the new identity.
  gate.resolve();
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(screen.queryByText("row-DOC")).toBeNull();
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
});

it("does not read while the session is loading or anonymous", async () => {
  const gate = deferred();
  let readCalls = 0;
  server.use(
    http.get("/api/session", async () => {
      await gate.promise;
      return HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 });
    }),
    http.get("/api/read/patients", () => {
      readCalls += 1;
      return HttpResponse.json({ items: [], total: 0 });
    }),
  );

  render(
    <AppProviders>
      <ReadProbe />
    </AppProviders>,
  );

  expect(screen.getByTestId("status")).toHaveTextContent("loading");
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(readCalls).toBe(0);

  gate.resolve();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("anonymous"),
  );
  expect(readCalls).toBe(0);
  expect(screen.getByTestId("rows")).toHaveTextContent("none");
  expect(screen.getByTestId("error")).toHaveTextContent("none");
});
