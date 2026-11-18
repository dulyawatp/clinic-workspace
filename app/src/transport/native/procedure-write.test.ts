import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  completeNativeProcedure,
  createNativeProcedure,
} from "./procedure-write";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const OPERATION = "operation-key-123456";
const WORKSPACE = "WS-PROC-NATIVE-1";
const NATIVE_DOCNAME = "PROC-0001";

function completeInput() {
  return { operation_key: OPERATION, procedure: WORKSPACE };
}

function createInput() {
  return { operation_key: OPERATION, service_request: "SR-0001" };
}

function completed(
  procedure: Record<string, unknown> = {
    procedure_id: WORKSPACE,
    state: "completed",
    status: "Completed",
  },
  stockEntryCreated = true,
) {
  return new Response(
    JSON.stringify({
      message: { status: "completed", procedure, stockEntryCreated },
    }),
    { status: 200 },
  );
}

function created(
  procedure: Record<string, unknown> = {
    procedure_id: NATIVE_DOCNAME,
    state: "pending",
    status: "Pending",
  },
) {
  return new Response(
    JSON.stringify({ message: { status: "created", procedure } }),
    { status: 200 },
  );
}

describe("native procedure completion", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it("posts one completion request and never sends an acknowledgement", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(completed());

    await expect(completeNativeProcedure(completeInput())).resolves.toEqual({
      status: "completed",
      procedure: {
        procedure_id: WORKSPACE,
        state: "completed",
        status: "Completed",
      },
      stockEntryCreated: true,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.procedures_write.complete",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      procedure: WORKSPACE,
    });
    expect(
      (fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>)[
        "X-Frappe-CSRF-Token"
      ],
    ).toBe("csrf");
    expect(localStorage.length).toBe(0);
  });

  it("keeps the readable stock flag from the server result", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(completed(undefined, false));

    await expect(
      completeNativeProcedure(completeInput()),
    ).resolves.toMatchObject({ stockEntryCreated: false });
  });

  it("accepts a native docname as the workspace target", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        completed({
          procedure_id: NATIVE_DOCNAME,
          state: "completed",
          status: "Completed",
        }),
      );

    await expect(
      completeNativeProcedure({ operation_key: OPERATION, procedure: NATIVE_DOCNAME }),
    ).resolves.toMatchObject({
      procedure: { procedure_id: NATIVE_DOCNAME },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      "an acknowledgement token",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "Completed",
          },
          stockEntryCreated: true,
          ackToken: "c".repeat(64),
        },
      },
    ],
    [
      "a missing stock flag",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "Completed",
          },
        },
      },
    ],
    [
      "a non-boolean stock flag",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "Completed",
          },
          stockEntryCreated: "true",
        },
      },
    ],
    [
      "another procedure id",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: "PROC-OTHER",
            state: "completed",
            status: "Completed",
          },
          stockEntryCreated: true,
        },
      },
    ],
    [
      "an unreadable state",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "pending",
            status: "Completed",
          },
          stockEntryCreated: true,
        },
      },
    ],
    [
      "an unreadable status",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "  ",
          },
          stockEntryCreated: true,
        },
      },
    ],
    [
      "a wrong operation status",
      {
        message: {
          status: "created",
          procedure: {
            procedure_id: WORKSPACE,
            state: "completed",
            status: "Completed",
          },
          stockEntryCreated: true,
        },
      },
    ],
  ])("reports %s as an unknown outcome", async (_label, body) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));

    await expect(completeNativeProcedure(completeInput())).rejects.toMatchObject(
      { code: "uncertain" },
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fails closed without a CSRF token and never dispatches", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(completeNativeProcedure(completeInput())).rejects.toMatchObject(
      { code: "uncertain" },
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats a lost response as an unknown outcome and never retries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("network"));

    await expect(completeNativeProcedure(completeInput())).rejects.toMatchObject(
      { code: "uncertain" },
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });
});

describe("native procedure creation", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it("posts the request selector and operation key, and returns the native docname", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(created());

    await expect(createNativeProcedure(createInput())).resolves.toEqual({
      status: "created",
      procedure: {
        procedure_id: NATIVE_DOCNAME,
        state: "pending",
        status: "Pending",
      },
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.procedures_write.create",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      service_request: "SR-0001",
    });
    expect(
      (fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>)[
        "X-Frappe-CSRF-Token"
      ],
    ).toBe("csrf");
    expect(localStorage.length).toBe(0);
  });

  it.each([
    [
      "an acknowledgement token",
      {
        message: {
          status: "created",
          procedure: {
            procedure_id: NATIVE_DOCNAME,
            state: "pending",
            status: "Pending",
          },
          ackToken: "c".repeat(64),
        },
      },
    ],
    [
      "a completion payload",
      {
        message: {
          status: "created",
          procedure: {
            procedure_id: NATIVE_DOCNAME,
            state: "pending",
            status: "Pending",
          },
          stockEntryCreated: false,
        },
      },
    ],
    [
      "a completed state",
      {
        message: {
          status: "created",
          procedure: {
            procedure_id: NATIVE_DOCNAME,
            state: "completed",
            status: "Completed",
          },
        },
      },
    ],
    [
      "an empty procedure id",
      {
        message: {
          status: "created",
          procedure: { procedure_id: "", state: "pending", status: "Pending" },
        },
      },
    ],
    [
      "a wrong operation status",
      {
        message: {
          status: "completed",
          procedure: {
            procedure_id: NATIVE_DOCNAME,
            state: "pending",
            status: "Pending",
          },
        },
      },
    ],
  ])("reports %s as an unknown outcome", async (_label, body) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));

    await expect(createNativeProcedure(createInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["an empty service request", { ...createInput(), service_request: "  " }],
    ["an empty operation key", { ...createInput(), operation_key: "" }],
  ])("rejects %s without dispatching", async (_label, input) => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(createNativeProcedure(input)).rejects.toMatchObject({
      code: "validation",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it("treats a lost response as an unknown outcome and never retries", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("network"));

    await expect(createNativeProcedure(createInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("native procedure refusals", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it.each([
    [409, "OPERATION_CONFLICT", "conflict"],
    [409, "WRITE_UNCERTAIN", "uncertain"],
    [422, "VALIDATION", "validation"],
    [417, "VALIDATION", "validation"],
    [403, "FORBIDDEN", "forbidden"],
    [401, "AUTH", "auth"],
    [500, "SERVER", "uncertain"],
  ])(
    "maps completion status %i code %s to %s without retrying",
    async (status, code, kind) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(
          new Response(JSON.stringify({ message: { code } }), { status }),
        );

      await expect(
        completeNativeProcedure(completeInput()),
      ).rejects.toMatchObject({ code: kind });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    [409, "OPERATION_CONFLICT", "conflict"],
    [409, "WRITE_UNCERTAIN", "uncertain"],
    [422, "VALIDATION", "validation"],
    [403, "FORBIDDEN", "forbidden"],
    [401, "AUTH", "auth"],
    [500, "SERVER", "uncertain"],
  ])(
    "maps creation status %i code %s to %s without retrying",
    async (status, code, kind) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(
          new Response(JSON.stringify({ message: { code } }), { status }),
        );

      await expect(createNativeProcedure(createInput())).rejects.toMatchObject({
        code: kind,
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );
});
