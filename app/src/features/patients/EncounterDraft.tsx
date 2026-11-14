import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { UNSAFE_DataRouterContext, useBlocker } from "react-router-dom";
import { useDirtyBeforeUnload } from "../../app/useDirtyBeforeUnload";
import { CSRF_HEADER, fetchCsrf } from "../../transport/auth";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { Button } from "../../ui/Button";
import { ReviewDetails } from "./ReviewDetails";

type Patient = { display_name: string; workspace_patient_id: string };
type Result = {
  state:
    | "ready"
    | "sending"
    | "unknown"
    | "created"
    | "error"
    | "validation"
    | "auth"
    | "forbidden"
    | "storage";
  key: string;
  id?: string;
};
type Stored = { key: string; state: "ready" | "unknown" | "created" };
type Draft = {
  request_key: string;
  patient_reference: string;
  created_at: string;
};
type DraftList = {
  scope: string;
  state: "loading" | "error" | "loaded";
  drafts: Draft[];
  truncated: boolean;
};

function NavigationGuard({
  dirty,
  warning,
}: {
  dirty: boolean;
  warning: string;
}) {
  const blocker = useBlocker(() => dirty && !window.confirm(warning));
  useEffect(() => {
    if (blocker.state === "blocked") blocker.reset();
  }, [blocker]);
  return null;
}

function storageKey(identity: string, reference: string) {
  return `encounter-draft:${JSON.stringify([identity, reference])}`;
}

function readStored(identity: string, reference: string): Stored | null {
  const value = sessionStorage.getItem(storageKey(identity, reference));
  if (value === null) return null;
  const data: unknown = JSON.parse(value);
  if (
    !data ||
    typeof data !== "object" ||
    !("key" in data) ||
    typeof data.key !== "string" ||
    !data.key ||
    !("state" in data) ||
    (data.state !== "ready" &&
      data.state !== "unknown" &&
      data.state !== "created")
  )
    throw new Error("Invalid draft state");
  return { key: data.key, state: data.state };
}

function writeStored(identity: string, reference: string, value: Stored) {
  sessionStorage.setItem(
    storageKey(identity, reference),
    JSON.stringify(value),
  );
  const saved = readStored(identity, reference);
  if (saved?.key !== value.key || saved.state !== value.state)
    throw new Error("Draft state not saved");
}

export function useEncounterDraft(identity: string | undefined) {
  const [selection, setSelection] = useState<{
    patient: Patient;
    identity: string;
  } | null>(null);
  const patient =
    selection && selection.identity === identity ? selection.patient : null;
  const [results, setResults] = useState<Record<string, Result>>({});
  const busy = useRef(false);
  const [sending, setSending] = useState(false);
  const [list, setList] = useState<DraftList | null>(null);
  const [retry, setRetry] = useState(0);
  const [opened, setOpened] = useState<{ scope: string; key: string } | null>(
    null,
  );
  const { t } = useTranslation();
  const [dirtyState, setDirtyState] = useState<{
    identity: string | undefined;
    value: boolean;
  }>({ identity, value: false });
  const dirty = dirtyState.identity === identity && dirtyState.value;
  const setDirty = useCallback(
    (value: boolean) => setDirtyState({ identity, value }),
    [identity],
  );
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  useDirtyBeforeUnload(dirty);

  const scope =
    patient && identity
      ? storageKey(identity, patient.workspace_patient_id)
      : null;
  useEffect(() => {
    if (!scope) return;
    // Retry triggers a fresh read for the same patient.
    void retry;
    const controller = new AbortController();
    setList({ scope, state: "loading", drafts: [], truncated: false });
    void (async () => {
      try {
        assertLegacyRoute();
        const response = await fetch("/api/live/encounter-drafts", {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Draft list unavailable");
        const body: unknown = await response.json();
        if (
          !body ||
          typeof body !== "object" ||
          !("drafts" in body) ||
          !Array.isArray(body.drafts) ||
          !("truncated" in body) ||
          typeof body.truncated !== "boolean" ||
          !body.drafts.every(
            (draft: unknown) =>
              draft &&
              typeof draft === "object" &&
              "request_key" in draft &&
              typeof draft.request_key === "string" &&
              !!draft.request_key &&
              "patient_reference" in draft &&
              typeof draft.patient_reference === "string" &&
              "created_at" in draft &&
              typeof draft.created_at === "string",
          )
        )
          throw new Error("Invalid draft list");
        if (!controller.signal.aborted)
          setList({
            scope,
            state: "loaded",
            drafts: body.drafts,
            truncated: body.truncated,
          });
      } catch {
        if (!controller.signal.aborted)
          setList({ scope, state: "error", drafts: [], truncated: false });
      }
    })();
    return () => controller.abort();
  }, [scope, retry]);

  function confirmDiscard() {
    return !dirty || window.confirm(t("reviewDetails.discardWarning"));
  }

  function select(patient: Patient) {
    if (!identity) return;
    if (
      selection?.identity === identity &&
      selection.patient.workspace_patient_id === patient.workspace_patient_id
    )
      return;
    if (!confirmDiscard()) return;
    setDirty(false);
    const reference = patient.workspace_patient_id;
    let result: Result;
    try {
      const stored = readStored(identity, reference);
      result = stored ?? { state: "ready", key: "" };
    } catch {
      result = { state: "storage", key: "" };
    }
    setResults((old) => ({
      ...old,
      [storageKey(identity, reference)]: result,
    }));
    setSelection({ patient, identity });
  }

  async function create() {
    if (!patient || !identity || busy.current) return;
    if (
      !scope ||
      list?.scope !== scope ||
      list.state !== "loaded" ||
      list.truncated ||
      list.drafts.some(
        (draft) => draft.patient_reference === patient.workspace_patient_id,
      )
    )
      return;
    const reference = patient.workspace_patient_id;
    const resultKey = storageKey(identity, reference);
    let stored: Stored | null;
    try {
      stored = readStored(identity, reference);
    } catch {
      setResults((old) => ({
        ...old,
        [resultKey]: { state: "storage", key: "" },
      }));
      return;
    }
    if (stored && stored.state !== "ready") return;
    let key: string;
    try {
      key = stored?.key ?? crypto.randomUUID();
    } catch {
      setResults((old) => ({
        ...old,
        [resultKey]: { state: "error", key: "" },
      }));
      return;
    }
    busy.current = true;
    setSending(true);
    setResults((old) => ({ ...old, [resultKey]: { state: "sending", key } }));
    let sent = false;
    let result: Result = { state: "error", key };
    try {
      assertLegacyRoute();
      const { token } = await fetchCsrf();
      // Persist the lock before dispatch: reload, crash, or storage failure must
      // never turn an in-flight request into a fresh creation intent.
      try {
        writeStored(identity, reference, { key, state: "unknown" });
      } catch {
        result = { state: "storage", key };
        return;
      }
      sent = true;
      const response = await fetch("/api/write/encounter-draft", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          [CSRF_HEADER]: token,
        },
        body: JSON.stringify({
          request_key: key,
          patient_reference: reference,
        }),
      });
      const body: unknown = await response.json().catch(() => null);
      if (
        response.ok &&
        body &&
        typeof body === "object" &&
        "status" in body &&
        body.status === "created" &&
        "encounter_id" in body &&
        typeof body.encounter_id === "string" &&
        body.encounter_id.trim()
      ) {
        result = { state: "created", key, id: body.encounter_id };
      } else if (
        !response.ok &&
        body &&
        typeof body === "object" &&
        "code" in body &&
        response.status === 400 &&
        body.code === "VALIDATION"
      ) {
        result = { state: "validation", key };
      } else if (
        !response.ok &&
        body &&
        typeof body === "object" &&
        "code" in body &&
        response.status === 401 &&
        body.code === "AUTH_EXPIRED"
      ) {
        result = { state: "auth", key };
      } else if (
        !response.ok &&
        body &&
        typeof body === "object" &&
        "code" in body &&
        response.status === 403 &&
        body.code === "FORBIDDEN"
      ) {
        result = { state: "forbidden", key };
      } else result = { state: "unknown", key };
    } catch {
      result = { state: sent ? "unknown" : "error", key };
    } finally {
      if (sent && result.state !== "unknown") {
        try {
          writeStored(identity, reference, {
            key,
            state: result.state === "created" ? "created" : "ready",
          });
        } catch {
          result = { state: "unknown", key };
        }
      }
      setResults((old) => ({ ...old, [resultKey]: result }));
      busy.current = false;
      setSending(false);
    }
  }

  const result =
    patient && identity
      ? results[storageKey(identity, patient.workspace_patient_id)]
      : undefined;
  const currentList = list?.scope === scope ? list : null;
  const matchingDrafts =
    currentList?.drafts.filter(
      (draft) => draft.patient_reference === patient?.workspace_patient_id,
    ) ?? [];
  const unsafe =
    sending || result?.state === "unknown" || result?.state === "storage";
  const reviewKey =
    opened?.scope === scope
      ? opened.key
      : result?.state === "created"
        ? result.key
        : null;
  const panel = patient && (
    <section
      className="patient-registration"
      aria-labelledby="encounter-draft-title"
    >
      <h3 id="encounter-draft-title">{t("encounterDraft.title")}</h3>
      {dataRouter && (
        <NavigationGuard
          dirty={dirty}
          warning={t("reviewDetails.discardWarning")}
        />
      )}
      <p>
        {t("encounterDraft.patient", {
          name: patient.display_name,
          reference: patient.workspace_patient_id,
        })}
      </p>
      <p>{t("encounterDraft.explanation")}</p>
      {result && result.state !== "sending" && result.state !== "ready" && (
        <p role={result.state === "created" ? "status" : "alert"}>
          {t(
            result.state === "created" && !result.id
              ? "encounterDraft.createdStored"
              : `encounterDraft.${result.state}`,
            { id: result.id },
          )}
        </p>
      )}
      <div aria-live="polite">
        {(!currentList || currentList.state === "loading") && (
          <p>{t("encounterDraft.listLoading")}</p>
        )}
        {currentList?.state === "error" && (
          <p role="alert">{t("encounterDraft.listError")}</p>
        )}
        {currentList?.state === "loaded" && currentList.truncated && (
          <p role="alert">{t("encounterDraft.listTruncated")}</p>
        )}
        {currentList?.state === "loaded" && matchingDrafts.length > 0 && (
          <div>
            <h4>{t("encounterDraft.existingDrafts")}</h4>
            <ul>
              {matchingDrafts.map((draft, index) => (
                <li key={draft.request_key}>
                  <span>
                    {t("encounterDraft.draftEntry", {
                      number: index + 1,
                      date: draft.created_at,
                    })}{" "}
                  </span>
                  <Button
                    variant="secondary"
                    disabled={unsafe}
                    onClick={() => {
                      if (!confirmDiscard()) return;
                      setDirty(false);
                      if (scope) setOpened({ scope, key: draft.request_key });
                    }}
                  >
                    {t("encounterDraft.reopen")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {(currentList?.state === "error" || currentList?.truncated) && (
          <Button variant="secondary" onClick={() => setRetry((n) => n + 1)}>
            {t("encounterDraft.retryList")}
          </Button>
        )}
      </div>
      {reviewKey && identity && !unsafe && (
        <ReviewDetails
          key={JSON.stringify([identity, reviewKey])}
          identity={identity}
          requestKey={reviewKey}
          onDirtyChange={setDirty}
        />
      )}
      {result?.state !== "created" &&
        result?.state !== "unknown" &&
        result?.state !== "storage" &&
        currentList?.state === "loaded" &&
        !currentList.truncated &&
        matchingDrafts.length === 0 && (
          <Button disabled={sending} onClick={() => void create()}>
            {t(sending ? "encounterDraft.sending" : "encounterDraft.confirm")}
          </Button>
        )}
      <Button
        variant="secondary"
        disabled={result?.state === "sending"}
        onClick={() => {
          if (!confirmDiscard()) return;
          setDirty(false);
          setSelection(null);
        }}
      >
        {t("encounterDraft.close")}
      </Button>
    </section>
  );
  return { select, panel };
}
