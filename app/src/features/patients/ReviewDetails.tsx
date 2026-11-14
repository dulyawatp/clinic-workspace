import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CSRF_HEADER, fetchCsrf } from "../../transport/auth";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { Button } from "../../ui/Button";

type Snapshot = { review_details: string; modified: string };

export function ReviewDetails({
  identity,
  requestKey,
  onDirtyChange,
}: {
  identity: string;
  requestKey: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [message, setMessage] = useState("loading");
  const [busy, setBusy] = useState(false);
  const [serverBlocked, setServerBlocked] = useState(false);
  const serverBlockedRef = useRef(false);
  const active = useRef(false);
  const mounted = useRef(false);
  const initialized = useRef(false);
  const lockKey = `encounter-review-lock:${JSON.stringify([identity, requestKey])}`;
  const [lockReason, setLockReason] = useState<"locked" | "unconfirmed" | null>(
    () => {
      try {
        const value = sessionStorage.getItem(lockKey);
        return value === null
          ? null
          : value === "unconfirmed"
            ? "unconfirmed"
            : "locked";
      } catch {
        return "locked";
      }
    },
  );
  const locked = lockReason !== null;
  const lockReasonRef = useRef(lockReason);
  lockReasonRef.current = lockReason;

  const load = useCallback(async () => {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    try {
      assertLegacyRoute();
      const response = await fetch(
        `/api/live/encounter-review?${new URLSearchParams({ request_key: requestKey })}`,
        {
          credentials: "same-origin",
          cache: "no-store",
          headers: { accept: "application/json" },
        },
      );
      const data: unknown = await response.json();
      if (
        !response.ok ||
        !data ||
        typeof data !== "object" ||
        !("review_details" in data) ||
        typeof data.review_details !== "string" ||
        !("modified" in data) ||
        typeof data.modified !== "string" ||
        !data.modified
      )
        throw new Error();
      const next = {
        review_details: data.review_details,
        modified: data.modified,
      };
      const blocked =
        "write_state" in data &&
        (data.write_state === "pending" || data.write_state === "unknown");
      serverBlockedRef.current = blocked;
      setServerBlocked(blocked);
      setSnapshot(next);
      setBaseline(next.review_details);
      if (!initialized.current) {
        setText(next.review_details);
        initialized.current = true;
      }
      setMessage(
        blocked ? "serverBlocked" : (lockReasonRef.current ?? "loaded"),
      );
    } catch {
      setMessage(
        serverBlockedRef.current
          ? "serverBlocked"
          : (lockReasonRef.current ?? "readError"),
      );
    } finally {
      active.current = false;
      setBusy(false);
    }
  }, [requestKey]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  useEffect(() => {
    onDirtyChange?.(baseline !== null && text !== baseline);
  }, [baseline, text, onDirtyChange]);

  async function save() {
    if (active.current || locked || serverBlocked || !snapshot) return;
    active.current = true;
    setBusy(true);
    let sent = false;
    try {
      assertLegacyRoute();
      const { token } = await fetchCsrf();
      if (!mounted.current) return;
      sessionStorage.setItem(lockKey, "unknown");
      if (sessionStorage.getItem(lockKey) !== "unknown") throw new Error();
      sent = true;
      const response = await fetch("/api/write/encounter-review", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          [CSRF_HEADER]: token,
        },
        body: JSON.stringify({
          request_key: requestKey,
          review_details: text,
          modified: snapshot.modified,
        }),
      });
      const data: unknown = await response.json().catch(() => null);
      if (
        response.ok &&
        data &&
        typeof data === "object" &&
        "status" in data &&
        data.status === "saved" &&
        "modified" in data &&
        typeof data.modified === "string" &&
        data.modified
      ) {
        sessionStorage.removeItem(lockKey);
        setSnapshot({ review_details: text, modified: data.modified });
        setBaseline(text);
        setMessage("saved");
      } else if (
        !response.ok &&
        response.status === 409 &&
        data &&
        typeof data === "object" &&
        "code" in data &&
        data.code === "INTENT_UNCONFIRMED"
      ) {
        sessionStorage.setItem(lockKey, "unconfirmed");
        setLockReason("unconfirmed");
        setMessage("unconfirmed");
      } else if (
        !response.ok &&
        data &&
        typeof data === "object" &&
        "code" in data &&
        ((response.status === 409 && data.code === "CONFLICT_STALE") ||
          (response.status === 409 && data.code === "INTENT_PENDING") ||
          (response.status === 409 && data.code === "UNSAFE_ENCOUNTER") ||
          (response.status === 400 && data.code === "VALIDATION") ||
          (response.status === 401 && data.code === "AUTH_EXPIRED") ||
          (response.status === 403 &&
            (data.code === "FORBIDDEN" || data.code === "WRITE_DISABLED")) ||
          (response.status === 404 &&
            (data.code === "NOT_FOUND" || data.code === "UNSUPPORTED")))
      ) {
        sessionStorage.removeItem(lockKey);
        if (data.code === "INTENT_PENDING") {
          setSnapshot(null);
          setMessage("pending");
        } else if (data.code === "CONFLICT_STALE") {
          setSnapshot(null);
          setMessage("stale");
        } else if (
          data.code === "UNSAFE_ENCOUNTER" ||
          data.code === "WRITE_DISABLED" ||
          data.code === "NOT_FOUND" ||
          data.code === "UNSUPPORTED"
        ) {
          setSnapshot(null);
          setMessage("refused");
        } else
          setMessage(
            response.status === 401
              ? "auth"
              : response.status === 403
                ? "forbidden"
                : "validation",
          );
      } else {
        setLockReason("locked");
        setMessage("locked");
      }
    } catch {
      if (sent) {
        setLockReason("locked");
        setMessage("locked");
      } else setMessage("writeError");
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="review-details-title"
      style={{ margin: "1.5rem 0", maxWidth: "48rem" }}
    >
      <h4 id="review-details-title">{t("reviewDetails.title")}</h4>
      <p>{t("reviewDetails.intro")}</p>
      <label htmlFor="review-details-text">{t("reviewDetails.label")}</label>
      <textarea
        id="review-details-text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        disabled={!initialized.current || busy}
        rows={8}
        style={{
          display: "block",
          width: "100%",
          boxSizing: "border-box",
          margin: ".5rem 0",
          resize: "vertical",
        }}
      />
      <p
        role={
          message === "saved" || message === "loaded" || message === "loading"
            ? "status"
            : "alert"
        }
      >
        {t(`reviewDetails.${message}`)}
      </p>
      {snapshot && initialized.current && text !== snapshot.review_details && (
        <details>
          <summary>{t("reviewDetails.serverVersion")}</summary>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {snapshot.review_details}
          </p>
        </details>
      )}
      <Button
        disabled={busy || locked || serverBlocked || !snapshot}
        onClick={() => void save()}
      >
        {t("reviewDetails.save")}
      </Button>{" "}
      <Button variant="secondary" disabled={busy} onClick={() => void load()}>
        {t("reviewDetails.reload")}
      </Button>
    </section>
  );
}
