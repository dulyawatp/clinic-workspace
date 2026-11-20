import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge } from "../../design/components";
import {
  createNativeEncounterDraft,
  listNativeEncounterAppointmentTypes,
  NativeEncounterDraftError,
  type NativeEncounterOption,
  readNativeEncounterPractitioner,
  searchNativeEncounterPatients,
} from "../../transport/native/encounter-draft";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { Button } from "../../ui/Button";
import { PageHeader } from "../../ui/PageHeader";
import { EncounterLayout } from "../clinical/ApprovedVisitWorkspace";

type ReadState<T> =
  | { state: "loading" | "denied" | "error" }
  | { state: "ready"; value: T };

type SaveState =
  | { state: "idle" | "pending" }
  | { state: "created"; id: string }
  | { state: "failed"; reason: NativeEncounterDraftError["reason"] };

const optionLabel = ({ id, label }: NativeEncounterOption) =>
  id === label ? label : `${label} · ${id}`;

const lookupFailure = (error: unknown) =>
  error instanceof NativeEncounterDraftError && error.reason === "denied" ? "denied" : "error";

export function NativeEncounterDraftPage() {
  const { session, authGeneration } = useSession();
  // The parent route owns authentication; this workflow only exists in native live mode.
  if (!nativeTransportEnabled() || session?.mode !== "live")
    return <Navigate to={ROUTES.home} replace />;
  return <NativeEncounterDraft key={authGeneration} userId={session.user.id} />;
}

function NativeEncounterDraft({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const inputId = useId();
  const searchHintId = useId();
  const patientSelectId = useId();
  const typeSelectId = useId();
  const input = useRef<HTMLInputElement>(null);
  const activeSearch = useRef<AbortController | null>(null);
  const saving = useRef(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<ReadState<NativeEncounterOption[]> | null>(null);
  const [practitioner, setPractitioner] = useState<ReadState<NativeEncounterOption>>({ state: "loading" });
  const [types, setTypes] = useState<ReadState<NativeEncounterOption[]>>({ state: "loading" });
  const [patientId, setPatientId] = useState("");
  const [typeId, setTypeId] = useState("");
  const [result, setResult] = useState<SaveState>({ state: "idle" });
  const pending = result.state === "pending";
  const frozen =
    pending ||
    result.state === "created" ||
    (result.state === "failed" && (result.reason === "unconfirmed" || result.reason === "conflict"));
  const patient = matches?.state === "ready" ? matches.value.find(({ id }) => id === patientId) : undefined;
  const type = types.state === "ready" ? types.value.find(({ id }) => id === typeId) : undefined;
  const canSave = !frozen && Boolean(patient && type && practitioner.state === "ready");

  useEffect(() => {
    const controller = new AbortController();
    void readNativeEncounterPractitioner(userId, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setPractitioner({ state: "ready", value });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setPractitioner({ state: lookupFailure(error) });
      },
    );
    void listNativeEncounterAppointmentTypes(controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setTypes({ state: "ready", value });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setTypes({ state: lookupFailure(error) });
      },
    );
    return () => {
      controller.abort();
      activeSearch.current?.abort();
    };
  }, [userId]);

  useEffect(() => {
    if (result.state === "idle") input.current?.focus();
  }, [result.state]);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = query.trim();
    if (!value || frozen || saving.current || matches?.state === "loading") return;
    activeSearch.current?.abort();
    const controller = new AbortController();
    activeSearch.current = controller;
    setPatientId("");
    setMatches({ state: "loading" });
    try {
      const options = await searchNativeEncounterPatients(value, controller.signal);
      if (!controller.signal.aborted) setMatches({ state: "ready", value: options });
    } catch (error) {
      if (!controller.signal.aborted) setMatches({ state: lookupFailure(error) });
    }
  }

  const patientHint = matches?.state === "loading" ? "common.loading"
    : matches?.state === "denied" ? "nativeEncounter.searchDenied"
    : matches?.state === "error" ? "nativeEncounter.searchError"
    : matches?.state === "ready" && !matches.value.length ? "search.noResults"
    : "nativeEncounter.searchHint";
  const typeHint = types.state === "loading" ? "common.loading"
    : types.state === "denied" ? "nativeEncounter.typesDenied"
    : types.state === "error" ? "nativeEncounter.typesError"
    : "nativeEncounter.typesUnavailable";
  const practitionerLabel = practitioner.state === "ready" ? optionLabel(practitioner.value)
    : t(practitioner.state === "loading" ? "nativeEncounter.practitionerLoading"
      : practitioner.state === "denied" ? "nativeEncounter.practitionerDenied"
      : "nativeEncounter.practitionerUnavailable");

  async function save() {
    if (saving.current || !canSave || !patient || !type || practitioner.state !== "ready") return;
    saving.current = true;
    setResult({ state: "pending" });
    try {
      const { id } = await createNativeEncounterDraft({
        userId,
        patientId: patient.id,
        practitionerId: practitioner.value.id,
        appointmentTypeId: type.id,
      });
      setResult({ state: "created", id });
    } catch (error) {
      setResult({
        state: "failed",
        reason: error instanceof NativeEncounterDraftError ? error.reason : "unconfirmed",
      });
    } finally {
      saving.current = false;
    }
  }

  const details = (
    <>
      <form className="patients-toolbar" onSubmit={(event) => void search(event)}>
        <div className="patients-field">
          <label htmlFor={inputId}>{t("nativeEncounter.search")}</label>
          <input
            ref={input}
            id={inputId}
            type="search"
            autoComplete="off"
            maxLength={100}
            value={query}
            disabled={frozen}
            aria-describedby={searchHintId}
            onChange={(event) => {
              activeSearch.current?.abort();
              setQuery(event.target.value);
              setMatches(null);
              setPatientId("");
            }}
          />
        </div>
        <Button
          type="submit"
          variant="secondary"
          disabled={frozen || !query.trim() || matches?.state === "loading"}
        >
          {t("action.search")}
        </Button>
      </form>
      <p className="patients-native-draft-hint" id={searchHintId} role="status">
        {t(patientHint)}
      </p>
      <div className="patients-write-fields">
        <div className="patients-field">
          <label htmlFor={patientSelectId}>{t("nativeEncounter.patient")}</label>
          <select
            id={patientSelectId}
            value={patientId}
            disabled={frozen || matches?.state !== "ready" || !matches.value.length}
            onChange={(event) => setPatientId(event.target.value)}
          >
            <option value="">{t("nativeEncounter.choosePatient")}</option>
            {matches?.state === "ready" && matches.value.map((option) => (
              <option key={option.id} value={option.id}>{optionLabel(option)}</option>
            ))}
          </select>
        </div>
        <div className="patients-field">
          <label htmlFor={typeSelectId}>{t("nativeEncounter.appointmentType")}</label>
          <select
            id={typeSelectId}
            value={typeId}
            disabled={frozen || types.state !== "ready" || !types.value.length}
            onChange={(event) => setTypeId(event.target.value)}
          >
            <option value="">{t("nativeEncounter.chooseAppointmentType")}</option>
            {types.state === "ready" && types.value.map((option) => (
              <option key={option.id} value={option.id}>{optionLabel(option)}</option>
            ))}
          </select>
        </div>
      </div>
      {(types.state !== "ready" || !types.value.length) && (
        <p className="patients-native-draft-hint" role="status">
          {t(typeHint)}
        </p>
      )}
      <p className="pc-note">{t("nativeEncounter.codedUnavailable")}</p>
      <details className="visit-preview-details">
        <summary>{t("workspace.moreDetails")}</summary>
        <p className="pc-note">{t("nativeEncounter.commentUnavailable")}</p>
      </details>
    </>
  );

  return (
    <>
      <PageHeader
        title={t("nativeEncounter.title")}
        actions={<Link className="ui-button ui-button--secondary" to={ROUTES.patients}>{t("nativeEncounter.back")}</Link>}
      />
      <div className="shell-page-body">
        <div className="visit-workspace visit-preview">
          <header className="visit-identity visit-preview-identity">
            <div className="visit-identity-heading">
              <span className="visit-avatar" aria-hidden="true">✚</span>
              <div>
                <p>{t("clinical.patient")}{patient && ` · ${patient.id}`}</p>
                <h2>{patient?.label ?? t("nativeEncounter.choosePatient")}</h2>
              </div>
            </div>
            <dl>
              <div><dt>{t("clinical.practitioner")}</dt><dd role="status">{practitionerLabel}</dd></div>
              <div><dt>{t("nativeEncounter.appointmentType")}</dt><dd>{type ? optionLabel(type) : "—"}</dd></div>
              <div><dt>{t("clinical.encounterId")}</dt><dd>{result.state === "created" ? result.id : "—"}</dd></div>
            </dl>
          </header>
          <p className="pc-note">{t("nativeEncounter.description")}</p>
          <EncounterLayout
            status={result.state === "created" ? <Badge tone="warning">{t("status.draft")}</Badge> : null}
            details={details}
            detailsLabel={t("workspace.details")}
            medications={<p className="pc-note">{t("nativeEncounter.medicationsUnavailable")}</p>}
          />
          <div className="visit-save-footer visit-preview-footer">
            <div className="pc-status-line">
              {result.state === "failed" ? (
                <p className="patients-native-draft-result" role="alert">{t(`nativeEncounter.${result.reason}`)}</p>
              ) : (
                <div role="status">
                  {result.state === "created" ? t("nativeEncounter.created", { id: result.id })
                    : t(pending ? "nativeEncounter.saving" : "workspace.draftUnsaved")}
                </div>
              )}
            </div>
            <div className="pc-row">
              <Button onClick={() => void save()} disabled={!canSave}>
                {t(pending ? "nativeEncounter.saving" : "nativeEncounter.save")}
              </Button>
              {result.state === "created" && (
                <>
                  <Link className="ui-button ui-button--secondary" to={ROUTES.visit(result.id)}>
                    {t("workspace.openEncounter")}
                  </Link>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setQuery("");
                      setMatches(null);
                      setPatientId("");
                      setTypeId("");
                      setResult({ state: "idle" });
                    }}
                  >
                    {t("nativeEncounter.startAnother")}
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
