import { frappeRequest } from "./frappe";

export interface ProcedureStatus {
  procedure_id: string;
  status: string;
  docstatus: number;
}

export class ProcedureReadError extends Error {
  constructor(readonly kind: "permission" | "unavailable") {
    super(kind);
  }
}

function parseItem(value: unknown): ProcedureStatus {
  if (!value || typeof value !== "object") throw new ProcedureReadError("unavailable");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== 3 ||
      typeof item.procedure_id !== "string" || !item.procedure_id.trim() ||
      typeof item.status !== "string" || !item.status.trim() ||
      typeof item.docstatus !== "number" || !Number.isInteger(item.docstatus)) {
    throw new ProcedureReadError("unavailable");
  }
  return { procedure_id: item.procedure_id, status: item.status, docstatus: item.docstatus };
}

async function read(method: string, query: URLSearchParams): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await frappeRequest(`clinic_ui.api.procedures_write.${method}?${query}`);
  } catch {
    throw new ProcedureReadError("unavailable");
  }
  if (response.status === 401 || response.status === 403) throw new ProcedureReadError("permission");
  if (!response.ok) throw new ProcedureReadError("unavailable");
  const body: unknown = await response.json().catch(() => null);
  if (!body || typeof body !== "object" || !("message" in body) ||
      !body.message || typeof body.message !== "object" || Array.isArray(body.message)) {
    throw new ProcedureReadError("unavailable");
  }
  return body.message as Record<string, unknown>;
}

export async function viewProcedure(id: string): Promise<ProcedureStatus> {
  const message = await read("view", new URLSearchParams({ procedure_id: id }));
  if (Object.keys(message).length !== 1) throw new ProcedureReadError("unavailable");
  return parseItem(message.procedure);
}

export async function findProcedures(serviceRequest: string): Promise<ProcedureStatus[]> {
  const message = await read("by_service_request", new URLSearchParams({ service_request: serviceRequest }));
  if (Object.keys(message).length !== 1 || !Array.isArray(message.items)) throw new ProcedureReadError("unavailable");
  return message.items.map(parseItem);
}
