import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../design/components";
import {
  captureVisitPhoto, changeVisitPhotoConsent, encodeVisitPhoto, readVisitPhotoOptions, readVisitPhotoStatus,
  type readVisitPhotoPair, type VisitPhotoFraming, type VisitPhotoSlot, type VisitPhotoSlotStatus, type VisitPhotoTarget,
  visitPhotoText,
} from "../../transport/native/visit-photos";

type Pair = NonNullable<Awaited<ReturnType<typeof readVisitPhotoPair>>>;
type Upload = {
  marker: symbol;
  slot: VisitPhotoSlot;
  key?: string;
  state: "sending" | "checking" | Exclude<VisitPhotoSlotStatus, "saved" | "rejected">;
};
type Activity = {
  revision: number;
  upload?: Upload;
  consent?: symbol;
  notice?: "saved" | "rejected" | "invalidFile" | "uploadUnavailable" | "consentRecorded" | "consentRevoked" | "consentUnknown";
};
const initial: Activity = { revision: 0 };

export function useVisitPhotoActivity(appointment: string) {
  const { authGeneration } = useSession();
  const client = useQueryClient();
  const queryKey = ["visit-photo-activity", authGeneration, appointment] as const;
  // Session-scoped memory only: no files, consent evidence or image bytes here.
  // Keep the guard across route/disclosure remounts until session cache is cleared.
  const { data } = useQuery({
    queryKey, initialData: initial, enabled: false, gcTime: Infinity, queryFn: () => initial,
  });
  function update(fn: (previous: Activity) => Activity) {
    client.setQueryData<Activity>(queryKey, (previous) => previous && fn(previous));
  }
  return { data, update };
}

export function ReceptionPhotoSteps({ consent, before }: { consent?: ReactNode; before?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="appointment-intake-grid">
      <div className="visit-preview-stack">
        <h3>{t("appointmentIntake.verify")}</h3>
        <p className="pc-note">{t("appointmentIntake.consentOptional")}</p>
        {consent}
      </div>
      <div className="visit-preview-stack">
        <h3>{t("appointmentIntake.before")}</h3>
        {before ?? <p className="pc-note">{t("workspace.nativePhotosUnavailable")}</p>}
      </div>
    </div>
  );
}

export function VisitPhotoCapture({ target, activity, pair, intakeBefore }: {
  target: VisitPhotoTarget;
  activity: ReturnType<typeof useVisitPhotoActivity>;
  pair?: Pair | null;
  intakeBefore?: ReactNode;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const { data, update } = activity;
  const options = useQuery({
    queryKey: ["visit-photo-options", authGeneration, target.appointment, target.encounter ?? null, data.revision],
    queryFn: ({ signal }) => readVisitPhotoOptions(target, signal),
    retry: false, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const flags = options.isFetchedAfterMount && !options.isFetching && !options.isError ? options.data : null;
  const upload = data.upload;

  async function submitConsent(evidence: string | null) {
    const marker = Symbol();
    let started = false;
    update((current) => {
      if (current.consent || current.notice === "consentUnknown" || current.revision !== data.revision) return current;
      started = true;
      return { ...current, consent: marker, notice: undefined };
    });
    if (!started) return;
    const ok = await changeVisitPhotoConsent(target.appointment, evidence).catch(() => false);
    update((current) => current.consent !== marker ? current : {
      ...current, consent: undefined, revision: current.revision + Number(ok),
      notice: ok ? (evidence === null ? "consentRevoked" : "consentRecorded") : "consentUnknown",
    });
  }

  async function submitPhoto(slot: VisitPhotoSlot, file: File, framing: VisitPhotoFraming) {
    const marker = Symbol();
    let started = false;
    update((current) => {
      if (current.upload || current.consent || current.notice === "consentUnknown" || current.revision !== data.revision) return current;
      started = true;
      return { ...current, upload: { marker, slot, state: "sending" }, notice: undefined };
    });
    if (!started) return;
    const image_base64 = await encodeVisitPhoto(file).catch(() => null);
    let key: string | undefined;
    update((current) => {
      if (current.upload?.marker !== marker) return current; // Auth changed during encoding.
      if (current.consent || current.revision !== data.revision || current.notice === "consentUnknown")
        return { ...current, upload: undefined };
      if (!image_base64) return { ...current, upload: undefined, notice: "invalidFile" };
      try {
        key = crypto.randomUUID();
      } catch {
        return { ...current, upload: undefined, notice: "uploadUnavailable" };
      }
      return { ...current, upload: { marker, slot, key, state: "sending" } };
    });
    if (!key || !image_base64) return;
    const state = await captureVisitPhoto({
      appointment: target.appointment,
      ...(slot === "After" && target.encounter ? { encounter: target.encounter } : {}),
      ...framing, slot, request_key: key, image_base64,
    }).catch(() => "unknown" as const);
    update((current) => current.upload?.marker === marker
      ? { ...current, upload: { ...current.upload, state } } : current);
  }

  async function checkUpload() {
    const key = upload?.key;
    if (!upload || !key) return;
    let started = false;
    update((current) => {
      const attempt = current.upload;
      if (attempt?.marker !== upload.marker || attempt.state === "sending" || attempt.state === "checking") return current;
      started = true;
      return { ...current, upload: { ...attempt, state: "checking" } };
    });
    if (!started) return;
    const state = await readVisitPhotoStatus({
      appointment: target.appointment, slot: upload.slot, request_key: key,
    }).catch(() => "unknown" as const);
    update((current) => {
      if (current.upload?.marker !== upload.marker) return current;
      // A not-found read can race a still-committing request; only a verified
      // terminal state releases this guard. Status never resubmits image bytes.
      if (state === "saved" || state === "rejected") return {
        ...current, upload: undefined, revision: current.revision + 1,
        notice: current.notice === "consentUnknown" ? current.notice : state,
      };
      return { ...current, upload: { ...current.upload, state } };
    });
  }

  const consentWritable = flags && !data.consent && data.notice !== "consentUnknown";
  // Intake uses server capture eligibility; it does not need generic pair access.
  const slot = consentWritable && flags.has_active_consent && !upload
    ? intakeBefore !== undefined ? (flags.can_capture_before ? "Before" : null)
      : pair && flags.can_capture_before && !pair.Before && !pair.After ? "Before"
        : pair && flags.can_capture_after && target.encounter && pair.framing && !pair.After ? "After" : null
    : null;
  const notice = data.notice && <p className="pc-note" role="status">{t(`workspace.visitPhotos.${data.notice}`)}</p>;
  const consentNotice = data.notice === "consentRecorded" || data.notice === "consentRevoked" || data.notice === "consentUnknown";
  const consent = <>
    {data.consent && <p className="pc-note" role="status">{t("common.loading")}</p>}
    {data.notice === "consentUnknown" && <div>
      <Button size="sm" variant="secondary" onClick={() => update((current) => ({
        ...current, revision: current.revision + 1, notice: undefined,
      }))}>{t("workspace.visitPhotos.checkConsent")}</Button>
    </div>}
  </>;
  const consentControls = <>
    {options.isFetching ? <p className="pc-note" role="status">{t("common.loading")}</p>
      : !flags && <p className="pc-note">{t("workspace.visitPhotos.controlsUnavailable")}</p>}
    {consentWritable && !flags.has_active_consent && flags.can_grant && <ConsentForm onGrant={submitConsent} />}
    {consentWritable && flags.has_active_consent && flags.can_revoke && <div className="visit-preview-stack">
      <p className="pc-note">{t("workspace.visitPhotos.activeConsent")}</p>
      <div><Button size="sm" variant="secondary" onClick={() => void submitConsent(null)}>
        {t("workspace.visitPhotos.revoke")}
      </Button></div>
    </div>}
  </>;
  const capture = <>
    {upload && <>
      <p className="pc-note" role="status">{t(`workspace.visitPhotos.upload.${upload.state}`)}</p>
      {upload.key && upload.state !== "sending" && upload.state !== "checking" && <div>
        <Button size="sm" variant="secondary" onClick={() => void checkUpload()}>{t("workspace.visitPhotos.check")}</Button>
      </div>}
    </>}
  </>;
  const captureForm = slot && <CaptureForm key={`${slot}:${data.revision}`} slot={slot} framing={pair?.framing ?? null} onCapture={submitPhoto} />;
  if (intakeBefore !== undefined) {
    return <ReceptionPhotoSteps consent={<>{consentNotice && notice}{consent}{consentControls}</>} before={<>
      {intakeBefore}
      {!consentNotice && notice}
      {capture}
      {captureForm}
    </>} />;
  }
  return (
    <div className="visit-preview-stack visit-photo-capture">
      {notice}
      {consent}
      {capture}
      {consentControls}
      {captureForm}
    </div>
  );
}

function ConsentForm({ onGrant }: { onGrant: (evidence: string) => Promise<void> }) {
  const { t } = useTranslation();
  const hint = useId();
  const [attested, setAttested] = useState(false);
  const [source, setSource] = useState("");
  const evidence = source.trim();
  const valid = attested && visitPhotoText(evidence, 3);
  return (
    <form className="visit-preview-stack" onSubmit={(event) => {
      event.preventDefault();
      if (valid) void onGrant(evidence);
    }}>
      <label className="visit-photo-attest">
        <input type="checkbox" checked={attested} onChange={(event) => setAttested(event.target.checked)} />
        <span>{t("workspace.visitPhotos.attest")}</span>
      </label>
      <label className="pc-field">
        <span className="pc-field__label">{t("workspace.visitPhotos.source")}</span>
        <input name="source" maxLength={140} aria-describedby={hint} value={source} onChange={(event) => setSource(event.target.value)} />
      </label>
      <small id={hint} className="pc-note">{t("workspace.visitPhotos.sourceHint")}</small>
      <div><Button size="sm" variant="primary" type="submit" disabled={!valid}>{t("workspace.visitPhotos.grant")}</Button></div>
    </form>
  );
}

function CaptureForm({ slot, framing, onCapture }: {
  slot: VisitPhotoSlot; framing: VisitPhotoFraming | null;
  onCapture: (slot: VisitPhotoSlot, file: File, frame: VisitPhotoFraming) => Promise<void>;
}) {
  const { t } = useTranslation();
  const hint = useId();
  const [invalid, setInvalid] = useState(false);
  const label = t(slot === "Before" ? "workspace.visitPhotos.before" : "workspace.visitPhotos.after");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement | null;
    const file = field("file")?.files?.[0];
    const frame = slot === "After" ? framing : {
      body_area: field("body_area")?.value.trim() ?? "", viewpoint: field("viewpoint")?.value.trim() ?? "",
    };
    if (!file || !frame || !visitPhotoText(frame.body_area) || !visitPhotoText(frame.viewpoint)) {
      setInvalid(true);
      return;
    }
    form.reset();
    void onCapture(slot, file, frame);
  }
  return (
    <form noValidate className="visit-preview-stack visit-photo-capture" onSubmit={submit}>
      {slot === "Before" ? <>
        <label className="pc-field"><span className="pc-field__label">{t("workspace.visitPhotos.bodyArea")}</span>
          <input name="body_area" required maxLength={140} /></label>
        <label className="pc-field"><span className="pc-field__label">{t("workspace.visitPhotos.viewpoint")}</span>
          <input name="viewpoint" required maxLength={140} /></label>
      </> : framing && <div className="visit-preview-stack">
        <p className="pc-note">{t("workspace.visitPhotos.matchBefore")}</p>
        <dl className="native-lab-facts">
          <div><dt>{t("workspace.visitPhotos.bodyArea")}</dt><dd>{framing.body_area}</dd></div>
          <div><dt>{t("workspace.visitPhotos.viewpoint")}</dt><dd>{framing.viewpoint}</dd></div>
        </dl>
      </div>}
      <label className="pc-field">
        <span className="pc-field__label">{t("workspace.visitPhotos.file", { photo: label })}</span>
        <input name="file" type="file" required accept="image/png,image/jpeg" aria-describedby={hint} />
      </label>
      <small id={hint} className="pc-note">{t("workspace.visitPhotos.fileHint")}</small>
      {invalid && <p className="pc-note" role="alert">{t("workspace.visitPhotos.invalidForm")}</p>}
      <div><Button size="sm" variant="primary" type="submit">{t("workspace.visitPhotos.save", { photo: label })}</Button></div>
    </form>
  );
}
