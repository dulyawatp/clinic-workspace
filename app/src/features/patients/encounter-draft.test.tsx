import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithCaps, renderWithFixtures } from "../../test/render";
import { useEncounterDraft } from "./EncounterDraft";
import { PatientDirectoryPage } from "./PatientDirectoryPage";

function setup(
  role: "DOC" | "REC" = "DOC",
  enabled = true,
  mode: "live" | "fixture" = "live",
) {
  const rendered = renderWithFixtures(<PatientDirectoryPage />, {
    role,
    sessionMode: mode,
    caps: {
      "read.patient": "enabled",
      "write.encounter.draft": enabled ? "enabled" : "denied",
    },
  });
  server.use(
    http.get("*/api/live/synthetic-patients", () =>
      HttpResponse.json({
        items: [
          {
            workspace_patient_id: "ref-1",
            native_patient_id: "native-1",
            display_name: "Example",
          },
        ],
      }),
    ),
  );
  return rendered;
}

beforeEach(() => {
  sessionStorage.clear();
  server.use(
    http.get("/api/live/encounter-drafts", () =>
      HttpResponse.json({ drafts: [], truncated: false }),
    ),
  );
});

it("shows an error without dispatch when request key generation fails", async () => {
  setup();
  const uuid = vi.spyOn(crypto, "randomUUID").mockImplementation(() => {
    throw new Error("unavailable");
  });
  let writes = 0;
  server.use(
    http.post("/api/write/encounter-draft", () => {
      writes++;
      return HttpResponse.json({ status: "unknown" });
    }),
  );
  try {
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: /สร้างร่างการตรวจสำหรับ Example/,
      }),
    );
    await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
    expect(screen.getByRole("alert")).toHaveTextContent("ไม่สามารถเริ่มคำขอได้");
    expect(writes).toBe(0);
  } finally {
    uuid.mockRestore();
  }
});

it.each([500, 502])(
  "treats HTTP %s as unknown and keeps the lock",
  async (status) => {
    const view = setup();
    server.use(
      http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
      http.post("/api/write/encounter-draft", () =>
        HttpResponse.json({ code: "UPSTREAM_UNAVAILABLE" }, { status }),
      ),
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: /สร้างร่างการตรวจสำหรับ Example/,
      }),
    );
    await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("อย่าลองซ้ำ");
    view.unmount();
    setup();
    await user.click(
      await screen.findByRole("button", {
        name: /สร้างร่างการตรวจสำหรับ Example/,
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("อย่าลองซ้ำ");
    expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
  },
);

function ConcurrentHarness() {
  const draft = useEncounterDraft("doctor");
  return (
    <>
      <button
        type="button"
        onClick={() =>
          draft.select({ display_name: "First", workspace_patient_id: "ref-1" })
        }
      >
        First
      </button>
      <button
        type="button"
        onClick={() =>
          draft.select({
            display_name: "Second",
            workspace_patient_id: "ref-2",
          })
        }
      >
        Second
      </button>
      {draft.panel}
    </>
  );
}

it("disables creation for another patient while a request is in flight", async () => {
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("/api/auth/csrf", async () => {
      await pending;
      return HttpResponse.json({ token: "token" });
    }),
    http.post("/api/write/encounter-draft", () =>
      HttpResponse.json({ status: "unknown" }),
    ),
  );
  renderWithCaps({}, <ConcurrentHarness />);
  const user = userEvent.setup();
  try {
    await user.click(screen.getByRole("button", { name: "First" }));
    await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
    await user.click(screen.getByRole("button", { name: "Second" }));
    expect(screen.getByRole("button", { name: "กำลังสร้างร่าง…" })).toBeDisabled();
  } finally {
    release?.();
  }
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "สร้างร่าง" })).toBeEnabled(),
  );
});

it.each([
  ["REC", true, "live"],
  ["DOC", false, "live"],
  ["DOC", true, "fixture"],
] as const)(
  "hides draft action for %s, capability %s, mode %s",
  async (role, enabled, mode) => {
    setup(role, enabled, mode);
    await screen.findByRole("table");
    expect(
      screen.queryByRole("button", { name: /สร้างร่างการตรวจสำหรับ/ }),
    ).toBeNull();
  },
);

it("sends CSRF and stable key after confirmed error, retaining patient context", async () => {
  setup();
  const requests: Array<{
    key: string;
    reference: string;
    csrf: string | null;
  }> = [];
  server.use(
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-draft", async ({ request }) => {
      const body = (await request.json()) as {
        request_key: string;
        patient_reference: string;
      };
      requests.push({
        key: body.request_key,
        reference: body.patient_reference,
        csrf: request.headers.get("x-clinic-csrf"),
      });
      return requests.length === 1
        ? HttpResponse.json({ code: "VALIDATION" }, { status: 400 })
        : HttpResponse.json({ status: "created", encounter_id: "enc-1" });
    }),
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /สร้างร่างการตรวจสำหรับ Example/ }),
  );
  await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
  await screen.findByRole("alert");
  expect(screen.getByText(/Example · ref-1/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
  expect(await screen.findByText(/enc-1/)).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual({
    key: requests[1]?.key,
    reference: "ref-1",
    csrf: "token",
  });
});

it("locks unknown outcome even after closing and reopening", async () => {
  setup();
  let count = 0;
  server.use(
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-draft", () => {
      count++;
      return HttpResponse.json({ status: "unknown" });
    }),
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /สร้างร่างการตรวจสำหรับ Example/ }),
  );
  await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("อย่าลองซ้ำ");
  await user.click(screen.getByRole("button", { name: "ปิด" }));
  await user.click(
    screen.getByRole("button", { name: /สร้างร่างการตรวจสำหรับ Example/ }),
  );
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
  await waitFor(() => expect(count).toBe(1));
});

it("keeps unknown lock and request key across remount without storing patient details", async () => {
  const view = setup();
  let key = "";
  server.use(
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/encounter-draft", async ({ request }) => {
      key = ((await request.json()) as { request_key: string }).request_key;
      return HttpResponse.json({ status: "unknown" });
    }),
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: /สร้างร่างการตรวจสำหรับ Example/ }),
  );
  await user.click(screen.getByRole("button", { name: "สร้างร่าง" }));
  await screen.findByRole("alert");
  const saved = Object.entries(sessionStorage).find(([name]) =>
    name.startsWith("encounter-draft:"),
  );
  expect(saved?.[1]).toContain(key);
  expect(JSON.stringify(saved)).not.toMatch(/Example|native-1|token/);
  view.unmount();
  setup();
  await user.click(
    await screen.findByRole("button", { name: /สร้างร่างการตรวจสำหรับ Example/ }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("อย่าลองซ้ำ");
  expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
});

it("fails closed when storage is unavailable before dispatch", async () => {
  setup();
  const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  try {
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: /สร้างร่างการตรวจสำหรับ Example/,
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("อย่าสร้างหรือลองซ้ำ");
    expect(screen.queryByRole("button", { name: "สร้างร่าง" })).toBeNull();
  } finally {
    spy.mockRestore();
  }
});

function Harness() {
  const [identity, setIdentity] = useState("doctor-a");
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
      <button type="button" onClick={() => setIdentity("doctor-b")}>
        Change identity
      </button>
      {draft.panel}
    </>
  );
}

it("isolates stored locks and selection when identity changes", async () => {
  sessionStorage.setItem(
    `encounter-draft:${JSON.stringify(["doctor-a", "ref-1"])}`,
    JSON.stringify({ key: "key-a", state: "unknown" }),
  );
  renderWithCaps({}, <Harness />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Select" }));
  expect(screen.getByRole("alert")).toHaveTextContent("อย่าลองซ้ำ");
  await user.click(screen.getByRole("button", { name: "Change identity" }));
  expect(screen.queryByRole("alert")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Select" }));
  expect(screen.getByRole("button", { name: "สร้างร่าง" })).toBeVisible();
});
