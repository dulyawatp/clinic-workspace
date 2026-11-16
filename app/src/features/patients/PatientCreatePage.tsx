import { useQueryClient } from "@tanstack/react-query";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge } from "../../design/components";
import { Button } from "../../design/components";
import { Card } from "../../design/components";
import { PageHeader } from "../../design/components";
import { SampleBanner } from "../../design/components";
import { consentForm } from "../../fixtures/data-clinical";
import { SAMPLE_LABEL } from "../../fixtures/data";
import { getCapability } from "../../domain/capability";
import type { PatientSummary } from "../../domain/patient";
import { formatDate } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { nativeTransportEnabled } from "../../transport/native/flag";
import {
  patientWriteLocked,
  saveNativePatient,
} from "../../transport/native/patient-write";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";
import { WriteGate } from "../../ui/WriteGate";
import { useCapabilities } from "../shell/CapabilityProvider";
import { useLivePatients } from "./live-directory";
import {
  bangkokTodayKey,
  type CreateField,
  type CreateFieldError,
  type CreateSex,
  type DirectoryCandidate,
  emptyCreateInput,
  findDuplicateCandidates,
  type PatientCreateInput,
  type SampleRegistration,
  saveSampleRegistration,
  clearCreateDraft,
  readCreateDraft,
  validatePatientCreate,
  writeCreateDraft,
} from "./patient-create";
import "./patients.css";

const SEX_OPTIONS: readonly CreateSex[] = ["female", "male", "other"];

const NATIVE_SEX: Readonly<Record<Exclude<CreateSex, "">, string>> = {
  female: "Female",
  male: "Male",
  other: "Other",
};

type Step =
  | { readonly kind: "edit" }
  | { readonly kind: "duplicate"; readonly matches: readonly DirectoryCandidate[] }
  /** `force` keeps the prototype's "register anyway" path past a duplicate. */
  | { readonly kind: "confirm"; readonly force: boolean }
  | { readonly kind: "checkFailed" }
  | { readonly kind: "saved"; readonly local: SampleRegistration | null };

type Lookup =
  | { readonly ok: true; readonly candidates: readonly DirectoryCandidate[] }
  | { readonly ok: false };

/** Reads the addressed directory once, on demand, for duplicate candidates. */
type CandidateLookup = () => Promise<Lookup>;

function ProxyCreateForm() {
  const query = useReadQuery<PatientSummary>("frontdesk-patients", {
    limit: 100,
  });
  const lookup = useCallback<CandidateLookup>(async () => {
    try {
      const result = query.isFetchedAfterMount ? query : await query.refetch();
      if (result.isError || result.data === undefined) return { ok: false };
      return {
        ok: true,
        candidates: result.data.items.map((row) => ({
          id: row.id,
          name: row.fullName,
          // The directory contract exposes a masked mobile, never the number.
          phone: row.mobileMasked,
        })),
      };
    } catch {
      return { ok: false };
    }
  }, [query]);
  return <CreateForm lookup={lookup} />;
}

function LiveCreateForm() {
  const query = useLivePatients();
  const lookup = useCallback<CandidateLookup>(async () => {
    try {
      const result = query.isFetchedAfterMount ? query : await query.refetch();
      if (result.isError || result.data === undefined) return { ok: false };
      return {
        ok: true,
        candidates: result.data.map((row) => ({
          id: row.workspace_patient_id,
          name: row.display_name,
        })),
      };
    } catch {
      return { ok: false };
    }
  }, [query]);
  return <CreateForm lookup={lookup} />;
}

export function PatientCreatePage() {
  return (
    <RequireSession>
      <CreateGate />
    </RequireSession>
  );
}

function CreateGate() {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const capability = getCapability(caps, "write.patient.create");

  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <>
        <PageHeader title={t("surface.PAT-02")} />
        <div className="shell-page-body">
          <StatePanel
            kind="permission-denied"
            title={t("gate.roleTitle")}
            detail={t("state.denied.body", { resource: t("surface.PAT-02") })}
          />
        </div>
      </>
    );
  if (capability === "error_upstream")
    return (
      <>
        <PageHeader title={t("surface.PAT-02")} />
        <div className="shell-page-body">
          <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
        </div>
      </>
    );

  return (
    <>
      <PageHeader
        title={t("surface.PAT-02")}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        breadcrumb={
          <>
            <Link to={ROUTES.patients}>{t("surface.PAT-01")}</Link>
            <span aria-hidden="true"> / </span>
            <span>{t("surface.PAT-02")}</span>
          </>
        }
      />
      <div className="shell-page-body">
        {session?.mode === "live" ? <LiveCreateForm /> : <ProxyCreateForm />}
      </div>
    </>
  );
}

function Field({
  id,
  labelKey,
  required = false,
  errorKey,
  hintKey,
  children,
}: {
  id: string;
  labelKey: string;
  required?: boolean;
  errorKey?: string;
  hintKey?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={[
        "patient-create-field",
        required ? "patient-create-field--required" : undefined,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <label htmlFor={id}>{t(labelKey)}</label>
      {children}
      {hintKey && <p className="patient-create-hint">{t(hintKey)}</p>}
      {errorKey && (
        <p className="patient-create-field-error" id={`${id}-error`}>
          {t(errorKey)}
        </p>
      )}
    </div>
  );
}

function CreateForm({ lookup }: { lookup: CandidateLookup }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { session } = useSession();
  const { caps } = useCapabilities();
  const queryClient = useQueryClient();
  const capability = getCapability(caps, "write.patient.create");
  const ids = useId();
  const [input, setInput] = useState<PatientCreateInput>(emptyCreateInput);
  const [step, setStep] = useState<Step>({ kind: "edit" });
  const [errors, setErrors] = useState<readonly CreateFieldError[]>([]);
  const [draft, setDraft] = useState<"none" | "loaded" | "saved" | "failed">(
    "none",
  );
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [locked, setLocked] = useState(patientWriteLocked);

  useEffect(() => {
    const stored = readCreateDraft();
    if (stored === null) return;
    setInput(stored);
    setDraft("loaded");
  }, []);

  const errorFor = useMemo(() => {
    const map = new Map<CreateField, string>();
    for (const error of errors) if (!map.has(error.field)) map.set(error.field, error.key);
    return map;
  }, [errors]);

  function update<K extends CreateField>(field: K, value: PatientCreateInput[K]) {
    // A write already in flight owns the snapshot it validated.
    if (saving) return;
    setInput((current) => ({ ...current, [field]: value }));
    setErrors((current) => current.filter((error) => error.field !== field));
    // An edited field invalidates the reviewed snapshot: confirm and duplicate
    // decisions must never be applied to input the reader has since changed.
    setStep({ kind: "edit" });
    setUncertain(false);
  }

  const displayName = `${input.firstName.trim()} ${input.lastName.trim()}`.trim();

  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reviewing || saving) return;
    setUncertain(false);
    const nextErrors = validatePatientCreate(input, bangkokTodayKey());
    setErrors(nextErrors);
    if (nextErrors.length > 0) {
      setStep({ kind: "edit" });
      return;
    }
    setReviewing(true);
    const result = await lookup();
    setReviewing(false);
    if (!result.ok) {
      setStep({ kind: "checkFailed" });
      return;
    }
    const matches = findDuplicateCandidates(input, result.candidates);
    setStep(
      matches.length > 0
        ? { kind: "duplicate", matches }
        : { kind: "confirm", force: false },
    );
  }

  async function confirm() {
    if (saving || reviewing || locked || capability !== "enabled") return;
    // The reviewed one-shot lock stays authoritative even if this page never
    // saw the earlier attempt: an ambiguous result disables a fresh write.
    if (patientWriteLocked()) {
      setLocked(true);
      return;
    }
    // Prototype parity: every register re-validates, and a non-forced register
    // re-runs the duplicate check against the directory.
    const nextErrors = validatePatientCreate(input, bangkokTodayKey());
    setErrors(nextErrors);
    if (nextErrors.length > 0) {
      setStep({ kind: "edit" });
      return;
    }
    if (step.kind === "confirm" && !step.force) {
      setReviewing(true);
      const result = await lookup();
      if (!result.ok) {
        setReviewing(false);
        setStep({ kind: "checkFailed" });
        return;
      }
      const matches = findDuplicateCandidates(input, result.candidates);
      if (matches.length > 0) {
        setReviewing(false);
        setStep({ kind: "duplicate", matches });
        return;
      }
      setReviewing(false);
    }
    setSaving(true);
    setUncertain(false);
    const native = session?.mode === "live" && nativeTransportEnabled();
    if (native && input.sex !== "") {
      try {
        await saveNativePatient({
          first_name: input.firstName.trim(),
          last_name: input.lastName.trim(),
          sex: NATIVE_SEX[input.sex],
          dob: input.dob,
          operation_key: crypto.randomUUID(),
        });
        clearCreateDraft();
        await queryClient.invalidateQueries({ queryKey: ["live-directory"] });
        await queryClient.invalidateQueries({ queryKey: ["live-patient"] });
        setStep({ kind: "saved", local: null });
      } catch {
        setUncertain(true);
        setLocked(patientWriteLocked());
      } finally {
        setSaving(false);
      }
      return;
    }
    const local = saveSampleRegistration(input);
    if (local === null) {
      setUncertain(true);
    } else {
      clearCreateDraft();
      setStep({ kind: "saved", local });
    }
    setSaving(false);
  }

  function saveDraft() {
    setDraft(writeCreateDraft(input) ? "saved" : "failed");
  }

  function clearDraft() {
    clearCreateDraft();
    setInput(emptyCreateInput());
    setErrors([]);
    setDraft("none");
    setStep({ kind: "edit" });
  }

  function restart() {
    setInput(emptyCreateInput());
    setErrors([]);
    setDraft("none");
    setStep({ kind: "edit" });
  }

  if (step.kind === "saved")
    return (
      <Card title={t("patientCreate.cardTitle")}>
        <SampleBanner label={t("app.demoTag")} title={t("patientCreate.sampleTitle")}>
          <p>{t("patientCreate.sampleBody")}</p>
        </SampleBanner>
        <p role="status" className="patient-create-success">
          {step.local === null
            ? t("patientCreate.createdNative")
            : t("patientCreate.createdLocal", { id: step.local.id })}
        </p>
        <dl className="patient-create-summary">
          <div>
            <dt>{t("field.first")}</dt>
            <dd>{displayName || "—"}</dd>
          </div>
          <div>
            <dt>{t("field.phone")}</dt>
            <dd>{input.phone}</dd>
          </div>
        </dl>
        <div className="patient-create-actions">
          <Button variant="primary" onClick={restart}>
            {t("patientCreate.registerAnother")}
          </Button>
          <Link className="pc-button pc-button--secondary" to={ROUTES.patients}>
            {t("patientCreate.backToDirectory")}
          </Link>
        </div>
      </Card>
    );

  return (
    <Card title={t("patientCreate.cardTitle")}>
      <SampleBanner label={t("app.demoTag")} title={t("patientCreate.sampleTitle")}>
        <p>{t("patientCreate.sampleBody")}</p>
        <p className="patient-create-note">{t("patientCreate.localOnlyNote")}</p>
      </SampleBanner>

      <form
        id={`${ids}-form`}
        className="patient-create-form"
        aria-label={t("surface.PAT-02")}
        onSubmit={(event) => void review(event)}
        noValidate
      >
        <p className="patient-create-hint">{t("patientCreate.requiredNote")}</p>

        <fieldset className="patient-create-group">
          <legend>{t("patientCreate.section.identity")}</legend>
          <div className="patient-create-grid">
            <Field
              id={`${ids}-first`}
              labelKey="field.first"
              required
              errorKey={errorFor.get("firstName")}
            >
              <input
                id={`${ids}-first`}
                value={input.firstName}
                maxLength={140}
                required
                aria-required="true"
                autoComplete="given-name"
                aria-invalid={errorFor.has("firstName") || undefined}
                onChange={(event) => update("firstName", event.target.value)}
              />
            </Field>
            <Field
              id={`${ids}-last`}
              labelKey="field.last"
              required
              errorKey={errorFor.get("lastName")}
            >
              <input
                id={`${ids}-last`}
                value={input.lastName}
                maxLength={140}
                required
                aria-required="true"
                autoComplete="family-name"
                aria-invalid={errorFor.has("lastName") || undefined}
                onChange={(event) => update("lastName", event.target.value)}
              />
            </Field>
            <Field
              id={`${ids}-sex`}
              labelKey="field.sex"
              required
              errorKey={errorFor.get("sex")}
            >
              <select
                id={`${ids}-sex`}
                value={input.sex}
                required
                aria-required="true"
                aria-invalid={errorFor.has("sex") || undefined}
                onChange={(event) =>
                  update("sex", event.target.value as CreateSex)
                }
              >
                <option value="">{t("patientCreate.select")}</option>
                {SEX_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {t(
                      `patient.${option === "female" ? "sexFemale" : option === "male" ? "sexMale" : "sexOther"}`,
                    )}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              id={`${ids}-dob`}
              labelKey="field.dob"
              required
              errorKey={errorFor.get("dob")}
            >
              <input
                id={`${ids}-dob`}
                type="date"
                value={input.dob}
                autoComplete="bday"
                required
                aria-required="true"
                aria-invalid={errorFor.has("dob") || undefined}
                onChange={(event) => update("dob", event.target.value)}
              />
            </Field>
          </div>
        </fieldset>

        <fieldset className="patient-create-group">
          <legend>{t("patientCreate.section.contact")}</legend>
          <div className="patient-create-grid">
            <Field
              id={`${ids}-phone`}
              labelKey="field.phone"
              required
              errorKey={errorFor.get("phone")}
              hintKey="patientCreate.phoneHint"
            >
              <input
                id={`${ids}-phone`}
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                value={input.phone}
                required
                aria-required="true"
                aria-invalid={errorFor.has("phone") || undefined}
                onChange={(event) => update("phone", event.target.value)}
              />
            </Field>
            <Field
              id={`${ids}-email`}
              labelKey="field.email"
              errorKey={errorFor.get("email")}
            >
              <input
                id={`${ids}-email`}
                type="email"
                autoComplete="email"
                value={input.email}
                aria-invalid={errorFor.has("email") || undefined}
                onChange={(event) => update("email", event.target.value)}
              />
            </Field>
            <Field id={`${ids}-address`} labelKey="field.address">
              <textarea
                id={`${ids}-address`}
                rows={2}
                value={input.address}
                onChange={(event) => update("address", event.target.value)}
              />
            </Field>
            <Field
              id={`${ids}-emergency`}
              labelKey="field.emergency"
              hintKey="patientCreate.emergencyHint"
            >
              <input
                id={`${ids}-emergency`}
                value={input.emergency}
                onChange={(event) => update("emergency", event.target.value)}
              />
            </Field>
          </div>
        </fieldset>

        <fieldset className="patient-create-group">
          <legend>{t("patientCreate.section.clinical")}</legend>
          <div className="patient-create-grid">
            <Field
              id={`${ids}-allergy`}
              labelKey="field.allergy"
              required
              errorKey={errorFor.get("allergyStatus")}
            >
              <select
                id={`${ids}-allergy`}
                value={input.allergyStatus}
                required
                aria-required="true"
                onChange={(event) =>
                  update(
                    "allergyStatus",
                    event.target.value as PatientCreateInput["allergyStatus"],
                  )
                }
              >
                <option value="unknown">{t("patientCreate.allergy.unknown")}</option>
                <option value="none">{t("patientCreate.allergy.none")}</option>
                <option value="known">{t("patientCreate.allergy.known")}</option>
              </select>
            </Field>
            <Field
              id={`${ids}-allergy-text`}
              labelKey="field.allergyText"
              errorKey={errorFor.get("allergyText")}
            >
              <input
                id={`${ids}-allergy-text`}
                value={input.allergyText}
                aria-invalid={errorFor.has("allergyText") || undefined}
                onChange={(event) => update("allergyText", event.target.value)}
              />
            </Field>
          </div>
        </fieldset>

        <fieldset className="patient-create-group patient-create-consent">
          <legend>{t("patientCreate.consentGroup")}</legend>
          <p>{t("patientCreate.consentDefer")}</p>
          <p className="patient-create-sample-line">
            <Badge tone="demo">{t("app.demoTag")}</Badge>{" "}
            {t("patientCreate.consentSample", {
              version: consentForm.version,
              scope: t("patientCreate.consentScope"),
            })}
          </p>
          <p className="patient-create-sr-note">{SAMPLE_LABEL}</p>
        </fieldset>

        {errors.length > 0 && (
          <div className="patient-create-errors" role="alert">
            <strong>{t("patientCreate.error.summary")}</strong>
            <ul>
              {errors.map((error, index) => (
                <li key={`${error.field}-${index}`}>{t(error.key)}</li>
              ))}
            </ul>
          </div>
        )}

        {step.kind === "edit" && (
          <div className="patient-create-actions">
            <Button onClick={saveDraft}>{t("action.saveDraft")}</Button>
            <Button
              type="submit"
              variant="primary"
              disabled={reviewing || saving}
            >
              {reviewing
                ? t("patientCreate.checking")
                : t("patientCreate.review")}
            </Button>
          </div>
        )}

        {step.kind === "checkFailed" && (
          <div className="patient-create-errors" role="alert">
            <strong>{t("patientCreate.duplicateCheckFailed")}</strong>
          </div>
        )}

        {step.kind === "duplicate" && (
          <div className="patient-create-duplicate" role="alert">
            <strong>{t("patientCreate.duplicateTitle")}</strong>
            <p>{t("patientCreate.duplicateBody")}</p>
            <ul>
              {step.matches.map((match) => (
                <li key={match.id}>
                  <Link to={ROUTES.patient(match.id)}>
                    {match.name} · {match.id}
                    {match.phone ? ` · ${match.phone}` : ""}
                  </Link>{" "}
                  <span className="patient-create-hint">
                    {t("patientCreate.duplicateOpen")}
                  </span>
                </li>
              ))}
            </ul>
            <div className="patient-create-actions">
              <Button
                variant="primary"
                onClick={() => setStep({ kind: "confirm", force: true })}
              >
                {t("action.createAnyway")}
              </Button>
              <Button
                variant="secondary"
                onClick={() => setStep({ kind: "edit" })}
              >
                {t("patientCreate.backToEdit")}
              </Button>
            </div>
          </div>
        )}

        {step.kind === "confirm" && (
          <div className="patient-create-confirm">
            <h3>{t("patientCreate.confirmTitle")}</h3>
            <dl className="patient-create-summary">
              <div>
                <dt>{t("field.first")}</dt>
                <dd>{displayName || "—"}</dd>
              </div>
              <div>
                <dt>{t("field.dob")}</dt>
                <dd>{formatDate(input.dob, locale)}</dd>
              </div>
              <div>
                <dt>{t("field.phone")}</dt>
                <dd>{input.phone}</dd>
              </div>
              <div>
                <dt>{t("field.emergency")}</dt>
                <dd>{input.emergency || "—"}</dd>
              </div>
            </dl>
            <p className="patient-create-note">
              {session?.mode === "live" && nativeTransportEnabled()
                ? t("patientCreate.confirmNative")
                : t("patientCreate.confirmLocal")}
            </p>
            <div className="patient-create-actions">
              <WriteGate
                capability="write.patient.create"
                reason={t("patients.registerBlocked")}
              >
                <button
                  type="button"
                  className="pc-button pc-button--primary"
                  disabled={saving || reviewing || locked}
                  onClick={() => void confirm()}
                >
                  {saving
                    ? t("patientWrite.saving")
                    : reviewing
                      ? t("patientCreate.checking")
                      : t("action.register")}
                </button>
              </WriteGate>
              <Button variant="secondary" onClick={() => setStep({ kind: "edit" })}>
                {t("patientCreate.backToEdit")}
              </Button>
            </div>
            {(uncertain || locked) && (
              <p role="alert" className="patient-create-status">
                {t("patientWrite.uncertain")}
              </p>
            )}
          </div>
        )}


        {draft === "loaded" && (
          <p role="status" className="patient-create-status">
            {t("patientCreate.draftLoaded")}
          </p>
        )}
        {draft === "saved" && (
          <p role="status" className="patient-create-status">
            {t("patientCreate.draftSaved")}
          </p>
        )}
        {draft === "failed" && (
          <p role="alert" className="patient-create-status">
            {t("patientCreate.draftFailed")}
          </p>
        )}
        {draft !== "none" && (
          <Button variant="ghost" size="sm" onClick={clearDraft}>
            {t("patientCreate.clearDraft")}
          </Button>
        )}

        {capability !== "enabled" && (
          <p className="patient-create-note">{t("patientCreate.writeClosed")}</p>
        )}
      </form>
    </Card>
  );
}
