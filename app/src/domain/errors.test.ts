import { describe, expect, it } from "vitest";
import enCatalog from "../i18n/catalog/en.json";
import thCatalog from "../i18n/catalog/th.json";
import { mapHttpError } from "../transport/error-map";
import { ERROR_CODES, fromWire, toI18nKey } from "./errors";

describe("error taxonomy", () => {
  it("maps AUTH_EXPIRED to a localisable key and marks it retryable after re-auth", () => {
    expect(toI18nKey({ code: "AUTH_EXPIRED", retryable: true })).toBe(
      "errors.AUTH_EXPIRED",
    );
  });

  it("maps any unrecognised wire code to UNKNOWN", () => {
    expect(fromWire("SOMETHING_NEW")).toBe("UNKNOWN");
    expect(fromWire(undefined)).toBe("UNKNOWN");
    expect(fromWire(42)).toBe("UNKNOWN");
  });

  it("falls back for unknown codes without exposing server text", () => {
    expect(toI18nKey({ code: "UNKNOWN", retryable: false })).toBe(
      "errors.UNKNOWN",
    );
  });

  it("has a catalogue entry for every error code", () => {
    for (const code of ERROR_CODES) {
      expect(thCatalog[`errors.${code}`], code).toBeTruthy();
      expect(enCatalog[`errors.${code}`], code).toBeTruthy();
    }
  });

  it("never includes a stack or server message in the client error", () => {
    const error = mapHttpError(400, {
      code: "VALIDATION",
      message:
        "Traceback (most recent call last): frappe.exceptions.ValidationError",
    });
    expect(JSON.stringify(error)).not.toMatch(/Traceback|frappe/i);
  });

  it("maps HTTP status and wire codes to safe client errors", () => {
    expect(mapHttpError(502, { message: "Traceback: frappe boom" })).toEqual({
      code: "UPSTREAM_UNAVAILABLE",
      retryable: true,
    });
    expect(mapHttpError(401, { code: "AUTH_EXPIRED" })).toEqual({
      code: "AUTH_EXPIRED",
      retryable: true,
    });
    expect(mapHttpError(409, { code: "CONFLICT_DUPLICATE" })).toEqual({
      code: "CONFLICT_DUPLICATE",
      retryable: false,
    });
    expect(
      mapHttpError(422, {
        code: "VALIDATION",
        fieldErrors: { email: "Enter a valid email address" },
      }),
    ).toEqual({
      code: "VALIDATION",
      retryable: false,
    });
    expect(mapHttpError(405, null)).toEqual({
      code: "UNSUPPORTED",
      retryable: false,
    });
    expect(mapHttpError(200, "not a body")).toEqual({
      code: "UNKNOWN",
      retryable: false,
    });
  });

  it("drops wire field errors instead of leaking PHI or tracebacks", () => {
    const error = mapHttpError(422, {
      code: "VALIDATION",
      fieldErrors: {
        note: "Traceback (most recent call last): frappe.exceptions",
        patient: "สมชาย ใจดี HN 000123",
        lab: "HIV positive",
        nested: { message: "nested patient data" },
        list: ["array patient data"],
      },
      message: "frappe server failure",
    });

    expect(error).toEqual({ code: "VALIDATION", retryable: false });
    expect(error.fieldErrors).toBeUndefined();
    expect(JSON.stringify(error)).not.toMatch(
      /Traceback|frappe|สมชาย|HN 000123|HIV|nested patient|array patient/i,
    );
  });

  it("ignores array and nested bodies without leaking their contents", () => {
    const sensitive = {
      code: "VALIDATION",
      fieldErrors: { note: "Traceback: frappe patient HN 000123" },
    };
    const bodies: unknown[] = [
      [sensitive],
      { error: sensitive },
      { payload: { code: "VALIDATION", fieldErrors: sensitive.fieldErrors } },
    ];

    for (const body of bodies) {
      const error = mapHttpError(422, body);
      expect(error).toEqual({ code: "VALIDATION", retryable: false });
      expect(JSON.stringify(error)).not.toMatch(/Traceback|frappe|HN 000123/);
    }
  });
});
