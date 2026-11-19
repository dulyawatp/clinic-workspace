import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createNativeEncounterDraft,
  listNativeEncounterAppointmentTypes,
  NativeEncounterDraftError,
  type NativeEncounterOption,
  readNativeEncounterPractitioner,
  searchNativeEncounterPatients,
} from "../../transport/native/encounter-draft";
import { Button } from "../../ui/Button";

type ReadState<T> =
  | { state: "loading" | "error" }
  | { state: "ready"; value: T };

type SaveState =
  | { state: "idle" | "pending" }
  | { state: "created"; id: string }
  | { state: "failed"; reason: NativeEncounterDraftError["reason"] };

const optionLabel = ({ id, label }: NativeEncounterOption) =>
  id === label ? label : `${label} · ${id}`;

export function NativeEncounterDraft({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const [open, setOpen] = useState(false);

  return (
    <section
      className="patients-card patients-native-draft"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
    >
      <div className="patients-native-draft-header">
        <div>
          <h2 id={titleId}>{t("nativeEncounter.title")}</h2>
          <p id={descriptionId}>{t("nativeEncounter.description")}</p>
        </div>
        {!open && (
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {t("nativeEncounter.launch")}
          </Button>
        )}
      </div>
      {open && <DraftFields userId={userId} />}
    </section>
  );
}

function DraftFields({ userId }: { userId: string }) {
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
      () => {
        if (!controller.signal.aborted) setPractitioner({ state: "error" });
      },
    );
    void listNativeEncounterAppointmentTypes(controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setTypes({ state: "ready", value });
      },
      () => {
        if (!controller.signal.aborted) setTypes({ state: "error" });
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
    } catch {
      if (!controller.signal.aborted) setMatches({ state: "error" });
    }
  }

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

  return (
    <div className="patients-native-draft-body">
      <p className="patients-native-draft-practitioner" role="status">
        {practitioner.state === "ready"
          ? t("nativeEncounter.practitionerReady", { name: optionLabel(practitioner.value) })
          : practitioner.state === "loading"
            ? t("nativeEncounter.practitionerLoading")
            : t("nativeEncounter.practitionerUnavailable")}
      </p>
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
        {matches?.state === "loading"
          ? t("common.loading")
          : matches?.state === "error"
            ? t("nativeEncounter.searchError")
            : matches?.state === "ready" && !matches.value.length
              ? t("search.noResults")
              : t("nativeEncounter.searchHint")}
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
          {types.state === "loading" ? t("common.loading") : t("nativeEncounter.typesUnavailable")}
        </p>
      )}
      <div className="patients-native-draft-actions">
        <Button onClick={() => void save()} disabled={!canSave}>
          {t(pending ? "nativeEncounter.saving" : "nativeEncounter.save")}
        </Button>
        {result.state === "created" && (
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
        )}
      </div>
      <div className="patients-native-draft-feedback" role="status">
        {pending && <p>{t("nativeEncounter.saving")}</p>}
        {result.state === "created" && (
          <p className="patients-native-draft-result">
            {t("nativeEncounter.created", { id: result.id })}
          </p>
        )}
      </div>
      {result.state === "failed" && (
        <p className="patients-native-draft-result" role="alert">
          {t(`nativeEncounter.${result.reason}`)}
        </p>
      )}
    </div>
  );
}
