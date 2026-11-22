import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { openLinkedEncounter } from "./linked-encounter";

const METHOD = "/api/method/clinic_ui.api.encounters.open_for_appointment";

function response(patient: string) {
  return {
    context: { appointment: "APPT-1", patient, practitioner: "DOC-1", status: "Open" },
    encounter: null,
    notes: { state: "available", items: [], next_offset: null },
    medications: { state: "available", complete: true, items: [] },
  };
}

async function open(patient: string) {
  server.use(http.get(METHOD, () => HttpResponse.json({ message: response(patient) })));
  return openLinkedEncounter("APPT-1", new AbortController().signal);
}

it("accepts a maximum-length opaque patient reference in linked context", async () => {
  const patient = `cpr1_${"a".repeat(803)}`;
  await expect(open(patient)).resolves.toMatchObject({ context: { patient } });
});

it("preserves legacy patient IDs up to 256 characters", async () => {
  const patient = "p".repeat(256);
  await expect(open(patient)).resolves.toMatchObject({ context: { patient } });
});

it.each(["cpr1_", "cpr1_invalid!", `cpr1_${"a".repeat(804)}`, "p".repeat(257)])(
  "rejects malformed or oversized patient identity %s",
  async (patient) => {
    await expect(open(patient)).rejects.toEqual({ code: "MALFORMED", retryable: false });
  },
);
