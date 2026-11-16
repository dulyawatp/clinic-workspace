import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";
import { AdminSettingsPage } from "./AdminSettingsPage";
import { AdminUsersPage } from "./AdminUsersPage";

const CREATE_USER = "/api/method/clinic_ui.api.admin_write.create_user";
const EDIT_MASTER = "/api/method/clinic_ui.api.admin_write.edit_master";
const ACK = "/api/method/clinic_ui.api.admin_write.ack";
const TOKEN = "a".repeat(64);

const ENABLED_USERS = {
  "write.admin.user": "enabled",
  "write.admin.master": "enabled",
} as const;

function serve(caps: Record<string, string>) {
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("ADM", caps)));
}

function ack() {
  return HttpResponse.json({ message: { acknowledged: true } });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  vi.restoreAllMocks();
});

it("hides the user form when the capability is read_only", async () => {
  serve({ "read.patient": "enabled" });
  renderWithFixtures(<AdminUsersPage />, {
    role: "ADM",
    sessionMode: "live",
  });
  await screen.findByTestId("admin-users");
  expect(screen.queryByRole("button", { name: "สร้างผู้ใช้" })).toBeNull();
});

it("creates one user with a caller-supplied password and acks once", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_USERS);
  server.use(
    http.post(CREATE_USER, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "created",
          user: {
            user_id: "sit.pha@example.invalid",
            roles: ["Stock Manager"],
            practitioner: null,
          },
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<AdminUsersPage />, {
    role: "ADM",
    sessionMode: "live",
    path: ROUTES.adminUsers,
  });
  await screen.findByTestId("admin-users");

  await user.click(screen.getByRole("button", { name: "สร้างผู้ใช้" }));
  await user.type(screen.getByLabelText("อีเมล"), "sit.pha@example.invalid");
  await user.type(screen.getByLabelText("ชื่อเต็ม"), "SIT Pharmacist");
  await user.type(screen.getByLabelText("รหัสผ่าน"), "caller-supplied-secret");
  await user.click(screen.getByRole("button", { name: "สร้างผู้ใช้" }));

  await waitFor(() => expect(bodies).toHaveLength(1));
  const body = bodies[0]!;
  expect(body.email).toBe("sit.pha@example.invalid");
  expect(body.full_name).toBe("SIT Pharmacist");
  expect(body.clinic_role_code).toBe("PHA");
  expect(body.password).toBe("caller-supplied-secret");
  expect(typeof body.operation_key).toBe("string");
  expect((body.operation_key as string).length).toBeGreaterThanOrEqual(16);
  expect(await screen.findByText("สร้างผู้ใช้แล้ว")).toBeVisible();
  expect(localStorage.getItem("clinic.admin-user-write.pending")).toBeNull();
  expect(JSON.stringify(localStorage)).not.toContain("caller-supplied-secret");
});

it("creates a warehouse from the masters surface", async () => {
  const user = userEvent.setup();
  const bodies: Record<string, unknown>[] = [];
  serve(ENABLED_USERS);
  server.use(
    http.post(EDIT_MASTER, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({
        message: {
          status: "created",
          master: { master_type: "warehouse", master_id: "SIT Pharmacy" },
          ackToken: TOKEN,
        },
      });
    }),
    http.post(ACK, ack),
  );
  renderWithFixtures(<AdminSettingsPage />, {
    role: "ADM",
    sessionMode: "live",
    path: `${ROUTES.adminSettings}?tab=warehouses`,
  });
  await screen.findByTestId("admin-settings");

  await user.click(screen.getByRole("button", { name: "สร้างคลัง" }));
  await user.type(screen.getByLabelText("ชื่อคลัง"), "SIT Pharmacy");
  await user.click(screen.getByRole("button", { name: "สร้างคลัง" }));

  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]!.master_type).toBe("warehouse");
  expect(bodies[0]!.payload).toEqual({ warehouse_name: "SIT Pharmacy" });
  expect(await screen.findByText("สร้างข้อมูลหลักแล้ว")).toBeVisible();
});

it("hides the master forms when the master capability is read_only", async () => {
  serve({ "write.admin.user": "enabled" });
  renderWithFixtures(<AdminSettingsPage />, {
    role: "ADM",
    sessionMode: "live",
    path: `${ROUTES.adminSettings}?tab=warehouses`,
  });
  await screen.findByTestId("admin-settings");
  expect(screen.queryByRole("button", { name: "สร้างคลัง" })).toBeNull();
});
