import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { coreData } from "../../fixtures/data";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import { server } from "../../test/msw/server";
import {
  NATIVE_BOOTSTRAP,
  nativeSession,
  postSpy,
} from "../../test/network-spy";
import { renderWithFixtures, setViewport } from "../../test/render";
import { DoctorHomePage } from "./DoctorHomePage";
import { LabWorkspacePage } from "./LabWorkspacePage";
import { followUpVisitsFor, labRequests, patientLabel } from "./sample-scope";

function renderDoctorHome(path = ROUTES.doctor) {
  return renderWithFixtures(
    <Routes>
      <Route path={ROUTES.doctor} element={<DoctorHomePage />} />
      <Route path={ROUTES.labPattern} element={<LabWorkspacePage />} />
    </Routes>,
    { role: "DOC", path },
  );
}

const PRACTITIONER = "HP-01";
const HOURS = "/api/method/clinic_ui.api.doctor_hours.get";
const hoursCard = () => within(screen.getByRole("region", { name: "My recurring working hours" }));
const READY = {
  state: "ready",
  schedules: [
    {
      serviceUnit: "Consultation 4",
      slots: [{ day: "Monday", from_time: "07:15:00", to_time: "11:45:00" }],
    },
    {
      slots: [{ day: "Friday", from_time: "14:20", to_time: "18:10" }],
    },
  ],
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

function mockAuthChannel() {
  const channel = new EventTarget();
  const close = vi.fn();
  const open = vi.fn(function () {
    return Object.assign(channel, { close });
  });
  vi.stubGlobal("BroadcastChannel", open);
  return {
    open,
    close,
    send(data: unknown) {
      act(() => { channel.dispatchEvent(new MessageEvent("message", { data })); });
    },
  };
}

function renderNativeDoctorHome(
  bootstrap: Parameters<typeof http.get>[1] = () => nativeSession("DOC"),
) {
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(http.get(NATIVE_BOOTSTRAP, bootstrap));
  function TestPage() {
    const { refresh } = useSession();
    return (
      <>
        <button type="button" onClick={() => void refresh()}>Refresh session</button>
        <DoctorHomePage />
      </>
    );
  }
  return renderWithFixtures(
    <LocaleProvider initial="en"><TestPage /></LocaleProvider>,
    { path: ROUTES.doctor, sessionMode: "live" },
  );
}

function expectedCounts() {
  const today = coreData.dates.todayKey();
  return {
    appointmentsToday: coreData.appointments.filter(
      (appointment) =>
        appointment.practitioner === PRACTITIONER &&
        appointment.date.slice(0, 10) === today,
    ).length,
    openDrafts: coreData.visits.filter(
      (visit) => visit.practitioner === PRACTITIONER && visit.state === "draft",
    ).length,
    labsAwaiting: labRequests.filter(
      (row) => row.practitioner === PRACTITIONER && row.status === "received",
    ).length,
    followUps: followUpVisitsFor(PRACTITIONER).length,
  };
}

it("recomputes the four work counts from the sample rows", async () => {
  renderDoctorHome();
  expect(await screen.findByTestId("sample-banner")).toBeVisible();
  const counts = expectedCounts();
  for (const [key, value] of Object.entries(counts)) {
    const card = screen.getByTestId(`kpi-${key}`);
    expect(card.querySelector(".pc-kpi__value")).toHaveTextContent(
      String(value),
    );
  }
});

it("shows the next patient and recent sample encounters, but no fixture hours", async () => {
  const fetchHours = vi.fn(() => HttpResponse.json({ message: READY }));
  server.use(http.get(HOURS, fetchHours));
  renderDoctorHome();
  const next = await screen.findByRole("heading", {
    name: patientLabel("P-10241", "th"),
  });
  expect(next).toBeVisible();
  expect(
    screen.getByRole("link", { name: /เปิดการตรวจ \/ ฉบับร่าง/ }),
  ).toHaveAttribute("href", ROUTES.visit("DC-VIS-2041"));

  const hours = await screen.findByTestId("doctor-hours");
  expect(hours).toHaveAttribute("data-state", "unavailable");
  expect(within(hours).queryByTestId("doctor-hours-slot")).toBeNull();
  expect(fetchHours).not.toHaveBeenCalled();

  expect(screen.getByRole("heading", { name: "การตรวจล่าสุด" })).toBeVisible();
  expect(
    screen.getAllByRole("link", { name: "เปิดการตรวจ" }).length,
  ).toBeGreaterThan(0);
});

it("links the review count to the first sample result", async () => {
  renderDoctorHome();
  const firstLab = labRequests.find(
    (row) => row.practitioner === PRACTITIONER && row.status === "received",
  );
  expect(screen.getByTestId("kpi-labsAwaiting")).toHaveAttribute(
    "href",
    ROUTES.lab(firstLab?.id ?? ""),
  );
  expect(screen.getByTestId("kpi-openDrafts")).toHaveAttribute(
    "href",
    "#doctorDrafts",
  );
  expect(screen.getByTestId("kpi-followUps")).toHaveAttribute(
    "href",
    "#doctorFollowUps",
  );
});

it.each([390, 834, 1440] as const)(
  "keeps the dense day table inside the scroll wrapper at %i",
  async (width) => {
    setViewport(width);
    renderDoctorHome();
    const table = await screen.findByRole("table", { name: "ผู้ป่วยวันนี้" });
    expect(table.parentElement).toHaveClass("pc-tablewrap");
    expect(screen.getByTestId("sample-banner")).toBeVisible();
  },
);

it("reads only its recurring hours while the rest of the native day stays sample", async () => {
  const network = postSpy();
  const requests: Request[] = [];
  const onStart = ({ request }: { request: Request }) => requests.push(request);
  server.events.on("request:start", onStart);
  const pending = deferred();
  const fetchHours = vi.fn(async () => {
    await pending.promise;
    return HttpResponse.json({ message: READY });
  });
  server.use(http.get(HOURS, fetchHours));
  renderNativeDoctorHome();
  try {
    const card = hoursCard();
    await waitFor(() => expect(fetchHours).toHaveBeenCalledTimes(1));
    expect(card.getByText("Loading your working hours…")).toBeVisible();
    const hours = card.getByTestId("doctor-hours");
    expect(hours).toHaveAttribute("data-state", "loading");
    expect(within(hours).queryByTestId("doctor-hours-slot")).toBeNull();
    expect(screen.getByTestId("sample-banner")).toBeVisible();
    pending.resolve();

    const slots = await card.findAllByTestId("doctor-hours-slot");
    expect(slots).toHaveLength(2);
    expect(slots[0]).toHaveTextContent(/Monday.*07:15–11:45/);
    expect(slots[1]).toHaveTextContent(/Friday.*14:20–18:10/);
    expect(within(hours).getByText("Consultation 4")).toBeVisible();
    expect(card.getByText(/do not show bookable times/i)).toBeVisible();
    expect(hours).not.toHaveTextContent(/HP-01|sample|breaks|leave/i);
    const reads = requests.filter((request) => new URL(request.url).pathname !== NATIVE_BOOTSTRAP);
    expect(reads).toHaveLength(1);
    const request = reads[0]!;
    expect(new URL(request.url).pathname + new URL(request.url).search).toBe(HOURS);
    expect(request.method).toBe("GET");
    expect(request.headers.get("X-Clinic-Workspace")).toBe("DOC");
    expect(request.headers.get("X-Clinic-Session-Epoch")).toBe("native-epoch");
    expect(request.body).toBeNull();
    expect(network.posts).toEqual([]);
  } finally {
    pending.resolve();
    network.stop();
    server.events.removeListener("request:start", onStart);
  }
});

it.each([
  ["explicit empty", { state: "empty" }, 200, "empty", "No recurring working hours are set."],
  ["explicit unavailable", { state: "unavailable" }, 200, "unavailable", "Your working hours are unavailable."],
  ["denied", READY, 403, "unavailable", "Your working hours are unavailable."],
  ["malformed ready", { ...READY, schedules: [] }, 200, "unavailable", "Your working hours are unavailable."],
] as const)("shows distinct hours feedback for %s", async (_case, message, status, state, text) => {
  server.use(http.get(HOURS, () => HttpResponse.json({ message }, { status })));
  renderNativeDoctorHome();
  const card = hoursCard();
  expect(await card.findByText(text)).toBeVisible();
  const hours = card.getByTestId("doctor-hours");
  expect(hours).toHaveAttribute("data-state", state);
  expect(within(hours).queryByTestId("doctor-hours-slot")).toBeNull();
});

it("hides loaded hours outside DOC and reads fresh on return", async () => {
  let role: "DOC" | "REC" = "DOC";
  const fetchHours = vi.fn(() => HttpResponse.json({ message: READY }));
  server.use(http.get(HOURS, fetchHours));
  renderNativeDoctorHome(() => nativeSession(role));
  const card = hoursCard();
  await card.findByText("Consultation 4");
  role = "REC";
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(card.queryByText("Consultation 4")).toBeNull();
  expect(await card.findByText("Your working hours are unavailable.")).toBeVisible();
  expect(fetchHours).toHaveBeenCalledTimes(1);

  role = "DOC";
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(await card.findByText("Consultation 4")).toBeVisible();
  expect(fetchHours).toHaveBeenCalledTimes(2);
});

it("discards an in-flight response when the browser epoch changes", async () => {
  let epoch = "first-epoch";
  const pending = deferred();
  const requestedEpochs: (string | null)[] = [];
  let oldSignal: AbortSignal | undefined;
  server.use(http.get(HOURS, async ({ request }) => {
    const requestedEpoch = request.headers.get("X-Clinic-Session-Epoch");
    requestedEpochs.push(requestedEpoch);
    if (requestedEpoch === "first-epoch") {
      oldSignal = request.signal;
      await pending.promise;
      return HttpResponse.json({ message: READY });
    }
    return HttpResponse.json({ message: { state: "empty" } });
  }));
  renderNativeDoctorHome(async () => {
    const response = await nativeSession("DOC").json();
    response.message.sessionEpoch = epoch;
    return HttpResponse.json(response);
  });
  const card = hoursCard();
  try {
    await waitFor(() => expect(requestedEpochs).toEqual(["first-epoch"]));
    epoch = "second-epoch";
    fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
    expect(await card.findByText("No recurring working hours are set.")).toBeVisible();
    pending.resolve();
    expect(oldSignal?.aborted).toBe(true);
    expect(requestedEpochs).toEqual(["first-epoch", "second-epoch"]);
    expect(card.queryByText("Consultation 4")).toBeNull();
  } finally {
    pending.resolve();
  }
});

it.each(["logout", "account replacement"] as const)(
  "clears hours on cross-tab %s before bootstrap returns",
  async (change) => {
    const channel = mockAuthChannel();
    const pending = deferred();
    let changed = false;
    const bootstrap = vi.fn(async () => {
      if (!changed) return nativeSession("DOC");
      await pending.promise;
      if (change === "logout") return new HttpResponse(null, { status: 401 });
      const { message } = await nativeSession("DOC").json();
      return HttpResponse.json({ message: {
        ...message,
        sessionEpoch: "replacement-epoch",
        user: { ...message.user, id: "native|replacement" },
      } });
    });
    const fetchHours = vi.fn(({ request }: { request: Request }) =>
      HttpResponse.json({ message:
        request.headers.get("X-Clinic-Session-Epoch") === "replacement-epoch"
          ? { state: "empty" }
          : READY,
      }),
    );
    server.use(http.get(HOURS, fetchHours));
    const view = renderNativeDoctorHome(bootstrap);
    const card = hoursCard();
    try {
      await card.findByText("Consultation 4");
      expect(channel.open).toHaveBeenCalledWith("clinic.native-auth");
      channel.send({ workspace: "REC" });
      expect(card.getByText("Consultation 4")).toBeVisible();
      expect(bootstrap).toHaveBeenCalledTimes(1);

      changed = true;
      channel.send("invalidate");
      expect(card.queryAllByTestId("doctor-hours-slot")).toHaveLength(0);
      expect(card.getByText("Loading your working hours…")).toBeVisible();
      await waitFor(() => expect(bootstrap).toHaveBeenCalledTimes(2));
      expect(fetchHours).toHaveBeenCalledTimes(1);
      pending.resolve();
      expect(await card.findByText(change === "logout"
        ? "Your working hours are unavailable."
        : "No recurring working hours are set.")).toBeVisible();
      expect(fetchHours).toHaveBeenCalledTimes(change === "logout" ? 1 : 2);
      expect(card.queryByText("Consultation 4")).toBeNull();
    } finally {
      pending.resolve();
      view.unmount();
    }
    expect(channel.close).toHaveBeenCalledOnce();
  },
);

it("keeps hours hidden after an ambiguous logout until a new epoch is validated", async () => {
  const channel = mockAuthChannel();
  let epoch = "old-epoch";
  const bootstrap = vi.fn(async () => {
    const { message } = await nativeSession("DOC").json();
    message.sessionEpoch = epoch;
    return HttpResponse.json({ message });
  });
  const fetchHours = vi.fn(({ request }: { request: Request }) => HttpResponse.json({
    message: request.headers.get("X-Clinic-Session-Epoch") === "old-epoch" ? READY : { state: "empty" },
  }));
  server.use(http.get(HOURS, fetchHours));
  renderNativeDoctorHome(bootstrap);
  const card = hoursCard();
  await card.findByText("Consultation 4");

  channel.send("invalidate-ambiguous");
  expect(card.queryAllByTestId("doctor-hours-slot")).toHaveLength(0);
  expect(await card.findByText("Your working hours are unavailable.")).toBeVisible();
  expect(bootstrap).toHaveBeenCalledTimes(2);
  fireEvent.focus(window);
  await waitFor(() => expect(bootstrap).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(card.getByText("Your working hours are unavailable.")).toBeVisible());
  expect(fetchHours).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(await card.findByText("Your working hours are unavailable.")).toBeVisible();
  expect(bootstrap).toHaveBeenCalledTimes(4);
  expect(fetchHours).toHaveBeenCalledTimes(1);

  epoch = "new-epoch";
  fireEvent.click(screen.getByRole("button", { name: "Refresh session" }));
  expect(await card.findByText("No recurring working hours are set.")).toBeVisible();
  expect(fetchHours).toHaveBeenCalledTimes(2);
});

it.each(["focus", "visibility"] as const)(
  "fails closed on %s resume until a later check confirms auth",
  async (resume) => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const pending = deferred();
    let phase: "ready" | "error" | "recovered" = "ready";
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const bootstrap = vi.fn(async () => {
      if (phase === "error") {
        await pending.promise;
        return new HttpResponse(null, { status: 503 });
      }
      const { message } = await nativeSession("DOC").json();
      // Exercise both negotiated and older one-role native sessions.
      if (resume === "focus") message.eligibleWorkspaces = ["DOC", "REC"];
      return HttpResponse.json({ message });
    });
    const fetchHours = vi.fn(() => HttpResponse.json({
      message: phase === "recovered" ? { state: "empty" } : READY,
    }));
    server.use(http.get(HOURS, fetchHours));
    renderNativeDoctorHome(bootstrap);
    const card = hoursCard();
    const notifyResume = () => {
      if (resume === "focus") {
        fireEvent.focus(window);
      } else {
        visibility.mockReturnValue("hidden");
        fireEvent(document, new Event("visibilitychange"));
        expect(card.queryAllByTestId("doctor-hours-slot")).toHaveLength(0);
        visibility.mockReturnValue("visible");
        fireEvent(document, new Event("visibilitychange"));
        fireEvent.focus(window);
      }
    };
    try {
      await card.findByText("Consultation 4");
      phase = "error";
      notifyResume();
      expect(card.queryAllByTestId("doctor-hours-slot")).toHaveLength(0);
      expect(card.getByText("Loading your working hours…")).toBeVisible();
      await waitFor(() => expect(bootstrap).toHaveBeenCalledTimes(2));
      expect(fetchHours).toHaveBeenCalledTimes(1);
      pending.resolve();
      expect(await card.findByText("Your working hours are unavailable.")).toBeVisible();
      expect(card.queryAllByTestId("doctor-hours-slot")).toHaveLength(0);
      expect(screen.getByTestId("sample-banner")).toBeVisible();
      expect(fetchHours).toHaveBeenCalledTimes(1);

      phase = "recovered";
      notifyResume();
      expect(await card.findByText("No recurring working hours are set.")).toBeVisible();
      expect(bootstrap).toHaveBeenCalledTimes(3);
      expect(fetchHours).toHaveBeenCalledTimes(2);
    } finally {
      pending.resolve();
    }
  },
);

it("keeps the sample label above the day rows", async () => {
  renderDoctorHome();
  const banner = await screen.findByTestId("sample-banner");
  expect(within(banner).getByText("ข้อมูลตัวอย่าง")).toBeVisible();
  expect(
    screen.queryByText(/ส่วนนี้ยังไม่พร้อม|this section is not available/i),
  ).toBeNull();
});

it("renders the designed English copy for the same sample day", async () => {
  renderWithFixtures(
    <LocaleProvider initial="en">
      <DoctorHomePage />
    </LocaleProvider>,
    { role: "DOC" },
  );
  expect(
    await screen.findByRole("heading", { name: "Doctor’s day" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "Next patient" })).toBeVisible();
  expect(screen.getAllByText(/Nattida Srisawat/).length).toBeGreaterThan(0);
});
