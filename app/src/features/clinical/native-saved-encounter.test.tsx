import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Outlet } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";

const list = "/api/method/clinic_ui.api.encounters.list_for_patient";
const view = "/api/method/clinic_ui.api.encounters.view";
const patientView = "/api/method/clinic_ui.api.patients.view";
const ref = "workspace-a";
const id = "DC-VIS-2041";
const row = {
  id,
  patientId: "PAT-native-A",
  patientName: "Real Patient A",
  practitioner: "Practitioner A",
  encounterDate: "2026-11-20",
  docstatus: 0,
};
const reply = (message: unknown) => HttpResponse.json({ message });
const record = (id: string, name = row.patientName) => ({
  ...row, id, patientName: name, symptoms: ["Headache"], diagnoses: [],
});

function deferred() {
  let finish!: () => void;
  const promise = new Promise<void>((resolve) => { finish = resolve; });
  return { promise, finish };
}

function RefreshControl() {
  const { refresh } = useSession();
  return <><button type="button" onClick={() => void refresh()}>Refresh identity</button><Outlet /></>;
}

function setup(path: string, refresh = false) {
  return renderWithDataRouter(
    refresh ? [{ element: <RefreshControl />, children: appRoutes }] : appRoutes,
    { initialEntries: [path], sessionMode: "live", role: "DOC" },
  );
}

beforeEach(() => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  localStorage.setItem(LOCALE_KEY, "en");
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("DOC")),
    http.get(patientView, ({ request }) => {
      const workspace_patient_id = new URL(request.url).searchParams.get("workspace_patient_id");
      if (workspace_patient_id === row.patientId) return new HttpResponse(null, { status: 403 });
      return reply({
        workspace_patient_id, display_name: `Patient profile ${workspace_patient_id}`,
        first_name: null, last_name: null, sex: null, dob: null, status: null,
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  localStorage.removeItem(LOCALE_KEY);
});

it("opens native patient history and reopens its saved encounter with GET only", async () => {
  const reads = vi.fn();
  const posts = postSpy();
  const first = setup(ROUTES.patient(ref));
  server.use(
    http.get(list, ({ request }) => {
      const params = new URL(request.url).searchParams;
      reads(params.get("patient_ref"), params.get("offset"));
      return params.get("offset") === "0"
        ? reply({ items: [row], nextOffset: params.get("patient_ref") === ref ? 20 : null })
        : reply({ items: [{ ...row, id: "ENCOUNTER-older", docstatus: 1 }], nextOffset: null });
    }),
    http.get(view, ({ request }) => {
      reads(new URL(request.url).searchParams.get("encounter_id"));
      return reply(record(id));
    }),
  );
  try {
    const user = userEvent.setup();
    const history = await screen.findByRole("link", { name: "Visit history" });
    expect(history).toHaveAttribute("href", `${ROUTES.patient(ref)}?view=history`);
    expect(reads).not.toHaveBeenCalled();
    await user.click(history);
    expect(await screen.findByRole("link", { name: `Visit ${id}` })).toHaveAttribute("href", ROUTES.visit(id));
    await user.click(screen.getByRole("button", { name: "Load more visits" }));
    expect(await screen.findByRole("link", { name: "Visit ENCOUNTER-older" })).toHaveAttribute("href", ROUTES.visit("ENCOUNTER-older"));
    await user.click(screen.getByRole("link", { name: `Visit ${id}` }));
    expect(first.router.state.location.pathname).toBe(ROUTES.visit(id));
    expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Clinical Note" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Lab Results" })).toBeVisible();
    expect(screen.getByText("Practitioner A")).toBeVisible();
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);
    expect(screen.getByText("Headache")).toBeVisible();
    expect(screen.getByText("No diagnoses recorded.")).toBeVisible();
    expect(screen.getByText("Clinical notes are not available here.")).toBeVisible();
    expect(screen.queryByTestId("sample-banner")).not.toBeInTheDocument();

    const address = first.router.state.location.pathname;
    const back = screen.getByRole("link", { name: "Visit history" });
    expect(back).toHaveAttribute("href", `${ROUTES.patient(row.patientId)}?view=history`);
    await user.click(back);
    expect(await screen.findByRole("link", { name: `Visit ${id}` })).toBeVisible();
    expect(first.router.state.location.pathname).toBe(ROUTES.patient(row.patientId));
    first.unmount();
    setup(address);
    expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
    expect(reads.mock.calls).toEqual([[ref, "0"], [ref, "20"], [id], [row.patientId, "0"], [id]]);
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it.each([
  [403, "Permission denied"],
  [404, "Visit not found"],
  [200, "Encounter could not be loaded."],
] as const)("rejects HTTP %s or mismatched encounter identity without fallback", async (status, title) => {
  setup(ROUTES.visit(id));
  server.use(http.get(view, () => status === 200
    ? reply(record("other-encounter"))
    : HttpResponse.json({ message: "DO NOT DISPLAY" }, { status })));
  expect(await screen.findByRole("heading", { name: title })).toBeVisible();
  expect(screen.queryByText("DO NOT DISPLAY")).not.toBeInTheDocument();
  expect(screen.queryByText(row.patientName)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Visit history" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("sample-banner")).not.toBeInTheDocument();
});

it("lets an authenticated non-DOC-02 role reach Frappe, but keeps explicit legacy writes guarded", async () => {
  const details = vi.fn();
  const { router } = setup(ROUTES.visit("ENC-actual"));
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("PHA")),
    http.get(view, ({ request }) => {
      const encounterId = new URL(request.url).searchParams.get("encounter_id");
      details(encounterId);
      return encounterId === "ENC-actual"
        ? reply({ ...record("ENC-actual"), diagnoses: null })
        : HttpResponse.json({ message: "denied by Frappe" }, { status: 403 });
    }),
  );
  expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
  expect(screen.getByText("Diagnoses are not available here.")).toBeVisible();

  await act(async () => router.navigate(ROUTES.visit("NO-ACCESS")));
  expect(await screen.findByRole("heading", { name: "Permission denied" })).toBeVisible();
  expect(screen.queryByTestId("role-guard-denied")).not.toBeInTheDocument();

  const alias = `wse1_${"a".repeat(32)}_${"b".repeat(16)}_legacy-draft-key-0001`;
  await act(async () => router.navigate(`${ROUTES.visit("ENC-1")}?patient=${ref}&encounter=${alias}`));
  expect(await screen.findByTestId("role-guard-denied")).toHaveAttribute("data-surface", "DOC-02");
  expect(details.mock.calls).toEqual([["ENC-actual"], ["NO-ACCESS"]]);
});

it("does not let a malformed legacy binding hijack a Thai native deep link", async () => {
  localStorage.setItem(LOCALE_KEY, "th");
  setup(`${ROUTES.visit("ENC-actual")}?patient=${ref}&appointment=wsa1_bad`);
  server.use(http.get(view, () => reply({ ...record("ENC-actual"), symptoms: [], diagnoses: null })));
  expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
  expect(screen.getByText("ยังไม่ได้บันทึกอาการ")).toBeVisible();
  expect(screen.getByText("ไม่สามารถแสดงการวินิจฉัยในหน้านี้")).toBeVisible();
  expect(screen.getByRole("link", { name: "ประวัติการตรวจ" })).toHaveAttribute(
    "href", `${ROUTES.patient(row.patientId)}?view=history`,
  );
  expect(screen.queryByTestId("native-write-banner")).not.toBeInTheDocument();
});

it("keeps the explicit legacy editor reachable for its existing role", async () => {
  const alias = `wsa1_${"c".repeat(32)}_${"d".repeat(16)}_appt-book-key-0001-012`;
  const posts = postSpy();
  const read = vi.fn();
  setup(`${ROUTES.visit("ENC-1")}?patient=${ref}&appointment=${alias}`);
  server.use(http.get(view, () => {
    read();
    return reply(record("ENC-1"));
  }));
  try {
    expect(await screen.findByTestId("native-visit-workspace")).toBeVisible();
    expect(read).not.toHaveBeenCalled();
    expect(posts.posts).toEqual([]);
  } finally {
    posts.stop();
  }
});

it("keeps the authored demo and proxy read on their existing branches", async () => {
  vi.stubEnv("VITE_CLINIC_NATIVE", undefined);
  const read = vi.fn();
  server.use(http.get(view, () => {
    read();
    return reply(record(id));
  }));
  const { router } = renderWithDataRouter(appRoutes, {
    initialEntries: [ROUTES.visit(id)], sessionMode: "fixture", role: "DOC",
  });
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  await act(async () => router.navigate(`${ROUTES.visit("ENC-1")}?section=note`));
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-1");
  expect(screen.queryByTestId("sample-banner")).not.toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
});

it("drops an in-flight history on patient change and a saved encounter on identity change", async () => {
  const delayed = deferred();
  const historyRequests = vi.fn();
  const { router } = setup(`${ROUTES.patient(ref)}?view=history`, true);
  server.use(
    http.get(list, async ({ request }) => {
      const patientRef = new URL(request.url).searchParams.get("patient_ref");
      historyRequests(patientRef);
      if (patientRef === ref) await delayed.promise;
      return reply({ items: patientRef === ref ? [row] : [], nextOffset: null });
    }),
    http.get(view, () => reply(record(id))),
  );
  await waitFor(() => expect(historyRequests).toHaveBeenCalledWith(ref));
  await act(async () => router.navigate(`${ROUTES.patient("workspace-b")}?view=history`));
  expect(await screen.findByText("No visits found.")).toBeVisible();
  await act(async () => delayed.finish());
  expect(screen.queryByRole("link", { name: `Visit ${id}` })).not.toBeInTheDocument();

  await act(async () => router.navigate(ROUTES.visit(id)));
  expect(await screen.findByRole("heading", { name: row.patientName })).toBeVisible();
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("NUR")),
    http.get(view, () => HttpResponse.json({ message: "denied" }, { status: 403 })),
  );
  await userEvent.setup().click(screen.getByRole("button", { name: "Refresh identity" }));
  expect(await screen.findByRole("heading", { name: "Permission denied" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: row.patientName })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Visit history" })).not.toBeInTheDocument();
});

it("does not render a late visit response after changing visit or show prior rows on denied history", async () => {
  const delayed = deferred();
  const requests = vi.fn();
  const { router } = setup(ROUTES.visit("ENC-older"));
  server.use(
    http.get(view, async ({ request }) => {
      const encounterId = new URL(request.url).searchParams.get("encounter_id");
      requests(encounterId);
      if (encounterId === "ENC-older") await delayed.promise;
      return reply(record(encounterId ?? "", encounterId === "ENC-older" ? "Older patient" : "Next patient"));
    }),
    http.get(list, () => HttpResponse.json({ message: "denied" }, { status: 403 })),
  );
  await waitFor(() => expect(requests).toHaveBeenCalledWith("ENC-older"));
  await act(async () => router.navigate(ROUTES.visit("ENC-next")));
  expect(await screen.findByRole("heading", { name: "Next patient" })).toBeVisible();
  await act(async () => delayed.finish());
  expect(screen.queryByText("Older patient")).not.toBeInTheDocument();
  await act(async () => router.navigate(`${ROUTES.patient(ref)}?view=history`));
  const history = await screen.findByRole("region", { name: "Visit history" });
  expect(await within(history).findByRole("heading", { name: "Permission denied" })).toBeVisible();
  expect(screen.queryByText("Next patient")).not.toBeInTheDocument();
});
