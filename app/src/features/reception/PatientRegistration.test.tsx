import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";
import en from "../../i18n/catalog/en.json";
import th from "../../i18n/catalog/th.json";
import { server } from "../../test/msw/server";
import { withFixtures } from "../../test/render";
import { PatientRegistration } from "./PatientRegistration";

it("submits server options with CSRF and displays returned ID", async () => {
  const page = withFixtures(<PatientRegistration />);
  const post = vi.fn(async ({ request }: { request: Request }) => {
    expect(request.headers.get("x-clinic-csrf")).toBe("token");
    expect(await request.json()).toMatchObject({
      first_name: "Jane",
      sex: "Female",
    });
    return HttpResponse.json({ status: "created", patient_id: "P-123" });
  });
  server.use(
    http.get("/api/live/patient-genders", () =>
      HttpResponse.json({ items: ["Female"] }),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/patient-create", post),
  );
  render(page);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", {
      name: /ลงทะเบียนผู้ป่วย|register patient/i,
    }),
  );
  await user.type(screen.getByLabelText(/ชื่อ$|first name/i), "Jane");
  await user.selectOptions(
    await screen.findByLabelText(/^เพศ$|^sex$/i),
    "Female",
  );
  await user.click(
    screen.getByRole("button", { name: /สร้างข้อมูลผู้ป่วย|create patient/i }),
  );
  expect(await screen.findByText(/P-123/)).toBeVisible();
  expect(post).toHaveBeenCalledOnce();
});

it("locks submission after an unknown outcome without retrying", async () => {
  const page = withFixtures(<PatientRegistration />);
  const post = vi.fn(() => HttpResponse.json({ status: "unknown" }));
  server.use(
    http.get("/api/live/patient-genders", () =>
      HttpResponse.json({ items: ["Other"] }),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/patient-create", post),
  );
  render(page);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", {
      name: /ลงทะเบียนผู้ป่วย|register patient/i,
    }),
  );
  await user.type(screen.getByLabelText(/ชื่อ$|first name/i), "Jane");
  await user.selectOptions(
    await screen.findByLabelText(/^เพศ$|^sex$/i),
    "Other",
  );
  await user.click(
    screen.getByRole("button", { name: /สร้างข้อมูลผู้ป่วย|create patient/i }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ไม่ทราบผล|outcome is unknown/i,
  );
  expect(
    screen.queryByRole("button", { name: /สร้างข้อมูลผู้ป่วย|create patient/i }),
  ).toBeNull();
  expect(post).toHaveBeenCalledOnce();
});

it.each([
  [400, "VALIDATION", /ตรวจสอบข้อมูล|check the entered details/i],
  [401, "AUTH_EXPIRED", /เซสชันหมดอายุ|session has expired/i],
  [403, "FORBIDDEN", /ไม่มีสิทธิ์|do not have permission/i],
])(
  "preserves details and intent key after definite %s error",
  async (status, code, message) => {
    const page = withFixtures(<PatientRegistration />);
    const keys: string[] = [];
    server.use(
      http.get("/api/live/patient-genders", () =>
        HttpResponse.json({ items: ["Other"] }),
      ),
      http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
      http.post("/api/write/patient-create", async ({ request }) => {
        const body = (await request.json()) as { request_key: string };
        keys.push(body.request_key);
        return HttpResponse.json({ code }, { status });
      }),
    );
    render(page);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: /ลงทะเบียนผู้ป่วย|register patient/i,
      }),
    );
    const first = screen.getByLabelText(/ชื่อ$|first name/i);
    await user.type(first, "Jane");
    await user.selectOptions(
      await screen.findByLabelText(/^เพศ$|^sex$/i),
      "Other",
    );
    const submit = screen.getByRole("button", {
      name: /สร้างข้อมูลผู้ป่วย|create patient/i,
    });
    await user.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(first).toHaveValue("Jane");
    expect(screen.getByLabelText(/^เพศ$|^sex$/i)).toHaveValue("Other");
    await user.click(submit);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  },
);

it("treats unverified HTTP failures as unknown and does not replay", async () => {
  const page = withFixtures(<PatientRegistration />);
  const post = vi.fn(() =>
    HttpResponse.json({ code: "VALIDATION" }, { status: 500 }),
  );
  server.use(
    http.get("/api/live/patient-genders", () =>
      HttpResponse.json({ items: ["Other"] }),
    ),
    http.get("/api/auth/csrf", () => HttpResponse.json({ token: "token" })),
    http.post("/api/write/patient-create", post),
  );
  render(page);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", {
      name: /ลงทะเบียนผู้ป่วย|register patient/i,
    }),
  );
  await user.type(screen.getByLabelText(/ชื่อ$|first name/i), "Jane");
  await user.selectOptions(
    await screen.findByLabelText(/^เพศ$|^sex$/i),
    "Other",
  );
  await user.click(
    screen.getByRole("button", { name: /สร้างข้อมูลผู้ป่วย|create patient/i }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ไม่ทราบผล|outcome is unknown/i,
  );
  expect(post).toHaveBeenCalledOnce();
});

it("provides definite and uncertain guidance in both languages without directing users to the directory", () => {
  expect(en["registration.unknown"]).toMatch(/source system/i);
  expect(th["registration.unknown"]).toContain("ระบบต้นทาง");
  expect(en["registration.unknown"]).not.toMatch(/directory/i);
  expect(th["registration.unknown"]).not.toContain("รายชื่อผู้ป่วย");
  for (const key of [
    "registration.validation",
    "registration.auth",
    "registration.forbidden",
  ] as const) {
    expect(en[key]).toContain("not created");
    expect(th[key]).toContain("ยังไม่ได้สร้างข้อมูลผู้ป่วย");
  }
});
