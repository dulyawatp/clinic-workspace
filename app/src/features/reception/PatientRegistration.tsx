import { type FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CSRF_HEADER, fetchCsrf } from "../../transport/auth";
import { assertLegacyRoute, legacyFetch } from "../../transport/native/legacy";
import { Button } from "../../ui/Button";

export function PatientRegistration() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [sex, setSex] = useState("");
  const [state, setState] = useState<
    | "ready"
    | "sending"
    | "unknown"
    | "created"
    | "error"
    | "validation"
    | "auth"
    | "forbidden"
  >("ready");
  const [patientId, setPatientId] = useState("");
  const active = useRef(true);
  const busy = useRef(false);
  const intentKeys = useRef(new Map<string, string>());

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoadError(false);
    legacyFetch("/api/live/patient-genders", {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const body: unknown = await response.json();
        if (
          !body ||
          typeof body !== "object" ||
          !("items" in body) ||
          !Array.isArray(body.items) ||
          !body.items.every(
            (item: unknown) => typeof item === "string" && item.trim(),
          )
        )
          throw new Error();
        setOptions(body.items);
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadError(true);
      });
    return () => controller.abort();
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current || !first.trim() || !options?.includes(sex)) return;
    busy.current = true;
    setState("sending");
    let sent = false;
    try {
      assertLegacyRoute();
      const { token } = await fetchCsrf();
      if (!active.current) return;
      const payload = {
        first_name: first.trim(),
        ...(last.trim() ? { last_name: last.trim() } : {}),
        sex,
      };
      const payloadKey = JSON.stringify(payload);
      let requestKey = intentKeys.current.get(payloadKey);
      if (!requestKey) {
        requestKey = crypto.randomUUID();
        intentKeys.current.set(payloadKey, requestKey);
      }
      sent = true;
      const response = await fetch("/api/write/patient-create", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          [CSRF_HEADER]: token,
        },
        body: JSON.stringify({ request_key: requestKey, ...payload }),
      });
      const body: unknown = await response.json();
      if (!active.current) return;
      if (
        response.ok &&
        body &&
        typeof body === "object" &&
        "status" in body &&
        body.status === "created" &&
        "patient_id" in body &&
        typeof body.patient_id === "string" &&
        body.patient_id.trim()
      ) {
        setPatientId(body.patient_id);
        setState("created");
      } else if (
        !response.ok &&
        body &&
        typeof body === "object" &&
        "code" in body
      ) {
        if (response.status === 400 && body.code === "VALIDATION")
          setState("validation");
        else if (response.status === 401 && body.code === "AUTH_EXPIRED")
          setState("auth");
        else if (response.status === 403 && body.code === "FORBIDDEN")
          setState("forbidden");
        else setState("unknown");
      } else setState("unknown");
    } catch {
      if (active.current) setState(sent ? "unknown" : "error");
    } finally {
      busy.current = false;
    }
  }

  function close() {
    setOpen(false);
    setOptions(null);
    setFirst("");
    setLast("");
    setSex("");
    setState("ready");
    setPatientId("");
  }
  if (!open)
    return (
      <Button onClick={() => setOpen(true)}>{t("action.register")}</Button>
    );
  return (
    <section
      className="patient-registration"
      aria-labelledby="registration-title"
    >
      <h3 id="registration-title">{t("action.register")}</h3>
      {state === "created" ? (
        <p role="status">{t("registration.created", { id: patientId })}</p>
      ) : state === "unknown" ? (
        <p role="alert">{t("registration.unknown")}</p>
      ) : (
        <form onSubmit={submit}>
          <div className="patient-registration-fields">
            <label>
              {t("registration.first")}{" "}
              <input
                required
                autoComplete="given-name"
                value={first}
                onChange={(e) => setFirst(e.target.value)}
              />
            </label>
            <label>
              {t("registration.last")}{" "}
              <input
                autoComplete="family-name"
                value={last}
                onChange={(e) => setLast(e.target.value)}
              />
            </label>
            <label>
              {t("registration.sex")}{" "}
              <select
                required
                value={sex}
                onChange={(e) => setSex(e.target.value)}
                disabled={!options || state === "sending"}
              >
                <option value="">{t("registration.select")}</option>
                {options?.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {loadError && <p role="alert">{t("registration.optionsError")}</p>}
          {state === "error" && <p role="alert">{t("registration.notSent")}</p>}
          {state === "validation" && (
            <p role="alert">{t("registration.validation")}</p>
          )}
          {state === "auth" && <p role="alert">{t("registration.auth")}</p>}
          {state === "forbidden" && (
            <p role="alert">{t("registration.forbidden")}</p>
          )}
          <Button
            type="submit"
            disabled={!options?.length || state === "sending"}
          >
            {state === "sending"
              ? t("registration.sending")
              : t("registration.submit")}
          </Button>
        </form>
      )}
      <Button
        variant="secondary"
        disabled={state === "sending"}
        onClick={close}
      >
        {t("registration.close")}
      </Button>
    </section>
  );
}
