import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createNativeProcedure,
  procedureCreateLocked,
  procedureWriteLocked,
} from "./procedure-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const input = { operation_key: "operation-1", service_request: "SR-0001" };
const ackToken = "c".repeat(64);
const CREATE_LOCK = "clinic.procedure-create-write.pending";

function success() {
  return new Response(
    JSON.stringify({
      message: {
        status: "created",
        ackToken,
        procedure: {
          procedure_id: "WS-PROC-NATIVE-1",
          state: "pending",
          status: "Pending",
        },
      },
    }),
    { status: 200 },
  );
}

describe("native procedure creation", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("locks before dispatch and keeps the create lock after an uncertain response", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        expect(procedureCreateLocked()).toBe(true);
        // The completion lock is a different key and stays untouched.
        expect(procedureWriteLocked()).toBe(false);
        throw new Error("network");
      });
    await expect(createNativeProcedure(input)).rejects.toBeDefined();
    expect(procedureCreateLocked()).toBe(true);
    await expect(
      createNativeProcedure({ ...input, operation_key: "operation-2" }),
    ).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("posts the request selector and operation key, then acks once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (url, init) => {
        expect(procedureCreateLocked()).toBe(true);
        expect(localStorage.getItem(CREATE_LOCK)).toBe("1");
        expect(JSON.stringify(localStorage)).not.toContain(ackToken);
        expect(
          (init?.headers as Record<string, string>)["X-Frappe-CSRF-Token"],
        ).toBe("csrf");
        if (String(url).endsWith(".create")) {
          expect(JSON.parse(String(init?.body))).toEqual(input);
          return success();
        }
        expect(String(url)).toBe(
          "/api/method/clinic_ui.api.procedures_write.ack",
        );
        expect(JSON.parse(String(init?.body))).toEqual({
          operation_key: input.operation_key,
          kind: "procedure_create",
          ack_token: ackToken,
        });
        return new Response(
          JSON.stringify({ message: { acknowledged: true } }),
          { status: 200 },
        );
      });
    const result = await createNativeProcedure(input);
    expect(result).toEqual({
      status: "created",
      procedure: {
        procedure_id: "WS-PROC-NATIVE-1",
        state: "pending",
        status: "Pending",
      },
    });
    expect(procedureCreateLocked()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects an empty service request without dispatching", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(
      createNativeProcedure({ ...input, service_request: "  " }),
    ).rejects.toMatchObject({ code: "validation" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(procedureCreateLocked()).toBe(false);
  });

  it("keeps the lock when the server refuses and never retries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ message: { code: "VALIDATION" } }), {
          status: 417,
        }),
      );
    await expect(createNativeProcedure(input)).rejects.toMatchObject({
      code: "validation",
    });
    expect(procedureCreateLocked()).toBe(true);
    await expect(createNativeProcedure(input)).rejects.toMatchObject({
      code: "locked",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["", "C".repeat(64), "c".repeat(63)])(
    "does not acknowledge an invalid token",
    async (token) => {
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(
          JSON.stringify({
            message: {
              status: "created",
              ackToken: token,
              procedure: { procedure_id: "x", state: "pending", status: "Pending" },
            },
          }),
          { status: 200 },
        ),
      );
      await expect(createNativeProcedure(input)).rejects.toBeDefined();
      expect(procedureCreateLocked()).toBe(true);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );
});
