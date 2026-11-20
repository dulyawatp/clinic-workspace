import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import {
  checkInAppointment,
  NativeCheckInError,
  type PractitionerOption,
  readCheckInAppointment,
  searchCheckInPractitioners,
} from "../../transport/native/appointment-check-in";
import { Button } from "../../ui/Button";

const DESK_CHECK_IN_STATUSES = new Set(["Open", "Confirmed", "No Show"]);

export function NativeAppointmentCheckIn({ nativeId }: { nativeId: string }) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const dispatched = useRef(false);
  const [outcome, setOutcome] = useState<
    { kind: "saved"; practitioner: string } | { kind: "denied" | "uncertain" } | null
  >(null);
  const nativeQueryKey = ["native-check-in-appointment", authGeneration, nativeId];
  const native = useQuery({
    queryKey: nativeQueryKey,
    queryFn: ({ signal }) => readCheckInAppointment(nativeId, signal),
    retry: false,
    gcTime: 0,
  });
  const eligible = !native.isError && native.data?.basedOnCheckIn === true &&
    DESK_CHECK_IN_STATUSES.has(native.data.status);

  async function submit(selection: PractitionerOption) {
    if (!eligible || dispatched.current) return;
    dispatched.current = true;
    setSaving(true);
    try {
      const saved = await checkInAppointment(nativeId, selection.id);
      queryClient.setQueryData(nativeQueryKey, saved);
      setOutcome({ kind: "saved", practitioner: selection.label });
    } catch (error) {
      setOutcome({
        kind: error instanceof NativeCheckInError && error.reason === "denied"
          ? "denied" : "uncertain",
      });
    }
    setOpen(false);
    setSaving(false);
    void queryClient.invalidateQueries({ queryKey: ["live-appointment"] });
    void queryClient.invalidateQueries({ queryKey: ["live-appointments"] });
  }

  if (outcome?.kind === "saved")
    return <p role="status" className="appointment-write-success">
      {t("nativeCheckIn.saved", { practitioner: outcome.practitioner })}
    </p>;
  if (outcome)
    return <p role="alert" className="appointment-write-alert">
      {t(outcome.kind === "denied" ? "appointmentWrite.forbidden" : "nativeCheckIn.uncertain")}
    </p>;

  return (
    <>
      {eligible && <Button onClick={() => setOpen(true)}>{t("action.checkIn")}</Button>}
      {!open && native.isError && (
        <p className="appointment-write-note" role="status">{t("appointmentWrite.eligibilityUnavailable")}</p>
      )}
      {open && <CheckInDialog
        assigned={native.data?.practitioner ?? null}
        eligible={eligible}
        saving={saving}
        onClose={() => { if (!dispatched.current) setOpen(false); }}
        onSubmit={(selection) => void submit(selection)}
      />}
    </>
  );
}

function CheckInDialog({
  assigned, eligible, saving, onClose, onSubmit,
}: {
  assigned: string | null;
  eligible: boolean;
  saving: boolean;
  onClose: () => void;
  onSubmit: (selection: PractitionerOption) => void;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<PractitionerOption | null>(
    assigned ? { id: assigned, label: assigned } : null,
  );
  const [search, setSearch] = useState("");
  const [settled, setSettled] = useState("");
  const text = search.trim();
  const searchEnabled = eligible && !saving && (!selected || text !== "");

  useEffect(() => {
    const dialog = dialogRef.current;
    const opener = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(text), 250);
    return () => window.clearTimeout(timer);
  }, [text]);

  const matches = useQuery({
    queryKey: ["native-check-in-practitioners", authGeneration, settled],
    queryFn: ({ signal }) => searchCheckInPractitioners(settled, signal),
    enabled: searchEnabled && settled === text,
    retry: false,
    gcTime: 0,
  });

  return (
    <dialog ref={dialogRef} className="appointment-check-in" aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <h2 id={titleId}>{t("action.checkIn")}</h2>
      <label className="pc-field">
        {t("nativeCheckIn.search")}
        <input type="search" value={search} disabled={!eligible || saving} maxLength={100}
          onChange={(event) => setSearch(event.target.value)} />
      </label>
      {selected && <p className="appointment-check-in-selection">
        {t("nativeCheckIn.selected")} <strong>{selected.label}</strong>
      </p>}
      {searchEnabled && (
        settled !== text || matches.isFetching ? <p role="status">{t("common.loading")}</p> :
        matches.isError ? <p role="alert">{t(
          matches.error instanceof NativeCheckInError && matches.error.reason === "denied"
            ? "nativeCheckIn.searchDenied" : "nativeCheckIn.searchUnavailable",
        )}</p> :
        matches.data?.length ? <ul className="appointment-check-in-matches">
          {matches.data.map((option) => (
            <li key={option.id}>
              <Button variant="secondary" onClick={() => { setSelected(option); setSearch(""); }}>
                {option.label}{" "}
                {option.label !== option.id && <small>{option.id}</small>}
              </Button>
            </li>
          ))}
        </ul> : <p role="status">{t("nativeCheckIn.noMatches")}</p>
      )}
      {!eligible && !saving && <p role="alert">{t("appointmentWrite.eligibilityUnavailable")}</p>}
      {saving && <p role="status">{t("nativeCheckIn.saving")}</p>}
      <div className="appointment-write-actions">
        <Button disabled={saving || !eligible || !selected}
          onClick={() => { if (selected) onSubmit(selected); }}>
          {t("appointmentWrite.checkinConfirmLabel")}
        </Button>
        <Button variant="secondary" disabled={saving} onClick={onClose}>
          {t("action.close")}
        </Button>
      </div>
    </dialog>
  );
}
