import { lazy, type ReactNode, Suspense, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import type { VisitAnnotationCallbacks } from "../../transport/native/visit-annotations";
import {
  type VisitPhotoSlot,
  type VisitPhotoTarget,
  readOwnBeforePhoto,
  readVisitPhotoAppointment,
  readVisitPhotoImage,
  readVisitPhotoPair,
} from "../../transport/native/visit-photos";
import { ReceptionPhotoSteps, useVisitPhotoActivity, VisitPhotoCapture } from "./VisitPhotoCapture";

const VisitAnnotationWorkspace = lazy(() => import("../photos/annotate/VisitAnnotationWorkspace")
  .then(({ VisitAnnotationWorkspace }) => ({ default: VisitAnnotationWorkspace })));

type Source = { appointment: string } | { encounterId: string; patientId: string };
const slots = ["Before", "After"] as const;
const slotKey = { Before: "workspace.visitPhotos.before", After: "workspace.visitPhotos.after" } as const;
type Pair = NonNullable<Awaited<ReturnType<typeof readVisitPhotoPair>>>;
type Selection = { slot: VisitPhotoSlot; trigger: HTMLButtonElement };
type Drawing = { callbacks: VisitAnnotationCallbacks; writesBlocked: boolean };
type DrawingEntry = Drawing & { encounter: string };

export function NativeVisitPhotos({ source, staffTarget, drawing, receptionIntake = false, comparison = false }: {
  source: Source; staffTarget?: VisitPhotoTarget; drawing?: Drawing; receptionIntake?: boolean; comparison?: boolean;
}) {
  const { t } = useTranslation();
  const { authGeneration, status, session } = useSession();
  if (status !== "authenticated" || !session) return null;
  const doctor = session.user.roles.includes("DOC");
  const staff = session.mode === "live" && (doctor || session.user.roles.includes("REC"));
  const target = staff && staffTarget && "appointment" in source && source.appointment === staffTarget.appointment
    ? { appointment: staffTarget.appointment, ...(doctor && staffTarget.encounter && { encounter: staffTarget.encounter }) }
    : undefined;
  if (receptionIntake) {
    if (!session.user.roles.includes("REC")) return null;
    return target && !staffTarget?.encounter
      ? <ReceptionVisitPhotos key={JSON.stringify([authGeneration, target.appointment])} appointment={target.appointment} />
      : <ReceptionPhotoSteps />;
  }
  return <VisitPhotoDisclosure key={JSON.stringify([authGeneration, source, target])} source={source}
    target={target} drawing={target?.encounter ? drawing : undefined} title={t("workspace.visitPhotos.open")}
    comparison={comparison && doctor && session.mode === "live"} />;
}

function VisitPhotoDisclosure({ source, title, target, drawing, comparison }: {
  source: Source; title: string; target?: VisitPhotoTarget; drawing?: Drawing; comparison: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="visit-photo-disclosure" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{title}</summary>
      {open && (target ? <StaffVisitPhotoPair target={target} drawing={drawing} comparison={comparison} />
        : <VisitPhotoPair source={source} comparison={comparison} includeFraming={comparison} />)}
    </details>
  );
}

function ReceptionVisitPhotos({ appointment }: { appointment: string }) {
  const { t } = useTranslation();
  const activity = useVisitPhotoActivity(appointment);
  const { consent, notice, revision } = activity.data;
  const before = consent || notice === "consentUnknown"
    ? <p className="pc-note" role="status">{t(consent ? "common.loading" : "workspace.nativePhotosUnavailable")}</p>
    : <ReceptionBeforePhoto key={revision} appointment={appointment} />;
  return <VisitPhotoCapture target={{ appointment }} activity={activity} intakeBefore={before} />;
}

function ReceptionBeforePhoto({ appointment }: { appointment: string }) {
  const { t } = useTranslation();
  const src = useVisitPhotoSource(appointment, "Before", true);
  const [failed, setFailed] = useState(false);
  const [preview, setPreview] = useState<HTMLButtonElement | null>(null);
  const title = t(slotKey.Before);
  if (!src || failed) {
    return <p className="pc-note" role="status">
      {t(src === undefined ? "common.loading" : "workspace.nativePhotosUnavailable")}
    </p>;
  }
  return <div className="visit-preview-stack">
    <div className="visit-photo-shot-stage" data-present="true">
      <img src={src} alt={title} onError={() => setFailed(true)} />
    </div>
    <div>
      <button type="button" className="pc-button pc-button--secondary" aria-haspopup="dialog"
        onClick={(event) => setPreview(event.currentTarget)}>
        {t("workspace.visitPhotos.view", { photo: title })}
      </button>
    </div>
    {preview && <VisitPhotoPreviewFrame title={title} trigger={preview} onClose={() => setPreview(null)}>
      <div className="native-lab-preview-stage">
        <img src={src} alt={title} onError={() => setFailed(true)} />
      </div>
    </VisitPhotoPreviewFrame>}
  </div>;
}

function StaffVisitPhotoPair({ target, drawing, comparison }: {
  target: VisitPhotoTarget; drawing?: Drawing; comparison?: boolean;
}) {
  const activity = useVisitPhotoActivity(target.appointment);
  const consentUncertain = Boolean(activity.data.consent) || activity.data.notice === "consentUnknown";
  return <VisitPhotoPair source={target} revision={activity.data.revision} includeFraming={Boolean(target.encounter) || comparison}
    drawing={drawing && target.encounter ? {
      ...drawing, encounter: target.encounter,
      writesBlocked: drawing.writesBlocked || consentUncertain,
    } : undefined}
    comparison={comparison}
    comparisonPaused={Boolean(activity.data.consent)} comparisonBlocked={activity.data.notice === "consentUnknown"}
    actions={(pair) => <VisitPhotoCapture target={target} activity={activity} pair={pair} />} />;
}

function VisitPhotoPair({ source, revision = 0, includeFraming = false, actions, drawing,
  comparison = false, comparisonPaused = false, comparisonBlocked = false }: {
  source: Source;
  revision?: number;
  includeFraming?: boolean;
  actions?: (pair: Pair | null | undefined) => ReactNode;
  drawing?: DrawingEntry;
  comparison?: boolean;
  comparisonPaused?: boolean;
  comparisonBlocked?: boolean;
}) {
  const { t } = useTranslation();
  const [result, setResult] = useState<{ revision: number; match: { appointment: string; pair: Pair } | null }>();
  const [selection, setSelection] = useState<Selection | null>(null);
  const [drawingSelection, setDrawingSelection] = useState<(Selection & { revision: number }) | null>(null);
  const selectDrawing = (selection: Selection) => setDrawingSelection({ ...selection, revision });
  const appointmentId = "appointment" in source ? source.appointment : null;
  const encounterId = "encounterId" in source ? source.encounterId : null;
  const patientId = "patientId" in source ? source.patientId : null;
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    async function load() {
      // Skip StrictMode's discarded effect before starting an authorized read.
      await Promise.resolve();
      if (signal.aborted) return;
      try {
        const appointment = appointmentId ?? (encounterId && patientId
          ? await readVisitPhotoAppointment(encounterId, patientId, signal) : null);
        if (signal.aborted) return;
        const pair = appointment ? await readVisitPhotoPair(appointment, signal, includeFraming) : null;
        if (!signal.aborted) setResult({ revision, match: appointment && pair ? { appointment, pair } : null });
      } catch {
        if (!signal.aborted) setResult({ revision, match: null });
      }
    }
    void load();
    return () => controller.abort();
  }, [appointmentId, encounterId, patientId, revision, includeFraming]);

  const match = result?.match;
  const loading = result === undefined || (comparison && (comparisonPaused || result.revision !== revision));
  const photos = (
    <>
      {loading ? <p className="pc-note" role="status">{t("common.loading")}</p>
        : !match || (comparison && comparisonBlocked) ? <p className="pc-note" role="status">{t("workspace.nativePhotosUnavailable")}</p>
          : !slots.some((slot) => match.pair[slot]) ? (
            <p className="pc-note">{t("workspace.visitPhotos.empty")}</p>
          )
            : comparison ? <VisitPhotoComparison key={JSON.stringify([revision, match.appointment])}
              appointment={match.appointment} pair={match.pair} drawingSlot={drawingSelection?.slot}
              onDrawing={drawing && match.pair.drawingsEnabled ? selectDrawing : undefined} />
            : <ul role="list" className="native-lab-list visit-preview-stack">
              {slots.map((slot) => (
                <li className="visit-preview-stack" key={slot}>
                  <strong>{t(slotKey[slot])}</strong>
                  {match.pair[slot] ? (
                    <div>
                      <button type="button" className="pc-button pc-button--secondary"
                        onClick={(event) => setSelection({ slot, trigger: event.currentTarget })}>
                        {t("workspace.visitPhotos.view", { photo: t(slotKey[slot]) })}
                      </button>
                      {drawing && match.pair.drawingsEnabled && result.revision === revision && <button type="button" className="pc-button pc-button--secondary"
                        aria-expanded={drawingSelection?.revision === revision && drawingSelection.slot === slot}
                         onClick={(event) => selectDrawing({ slot, trigger: event.currentTarget })}>
                        {t("annotation.visit.open", { photo: t(slotKey[slot]) })}
                      </button>}
                    </div>
                  ) : <p className="pc-note">{t("workspace.visitPhotos.emptySlot")}</p>}
                </li>
              ))}
            </ul>}
      {match && selection && <VisitPhotoPreview key={JSON.stringify([match.appointment, selection.slot])}
        appointment={match.appointment} selection={selection} onClose={() => setSelection(null)} />}
      {drawing && drawingSelection?.revision === revision && (!comparison || (!comparisonPaused && !comparisonBlocked)) &&
        result?.revision === revision && match?.appointment === appointmentId && match.pair.drawingsEnabled &&
        match.pair[drawingSelection.slot] &&
        <VisitPhotoDrawing key={JSON.stringify([revision, appointmentId, drawing.encounter, drawingSelection.slot])}
          appointment={match.appointment} selection={drawingSelection} drawing={drawing}
          onClose={() => setDrawingSelection(null)} />}
    </>
  );
  const controls = actions?.(result?.revision === revision ? match?.pair ?? null : undefined);
  return <>{photos}{controls}</>;
}

function VisitPhotoComparison({ appointment, pair, drawingSlot, onDrawing }: {
  appointment: string; pair: Pair; drawingSlot?: VisitPhotoSlot; onDrawing?: (selection: Selection) => void;
}) {
  const { t } = useTranslation();
  const before = useVisitPhotoSource(pair.Before ? appointment : null, "Before");
  const after = useVisitPhotoSource(pair.After ? appointment : null, "After");
  const originals = { Before: before, After: after };
  const [failed, setFailed] = useState<Partial<Record<VisitPhotoSlot, boolean>>>({});
  const [preview, setPreview] = useState<{ slot: VisitPhotoSlot | "pair"; trigger: HTMLButtonElement } | null>(null);
  const srcFor = (slot: VisitPhotoSlot) => failed[slot] ? null : originals[slot];
  const fail = (slot: VisitPhotoSlot) => {
    setFailed((previous) => ({ ...previous, [slot]: true }));
  };
  const complete = pair.Before && pair.After;
  const ready = Boolean(srcFor("Before") && srcFor("After"));
  const framing = pair.framing && `${pair.framing.body_area} · ${pair.framing.viewpoint}`;
  const singleSlot = preview && preview.slot !== "pair" ? preview.slot : null;
  const title = singleSlot ? t(slotKey[singleSlot]) : t("workspace.visitPhotos.compareTitle");

  function shot(slot: VisitPhotoSlot, interactive: boolean) {
    const src = srcFor(slot);
    const label = t(slotKey[slot]);
    const message = !pair[slot] ? "workspace.visitPhotos.emptySlot"
      : src === undefined ? "common.loading" : "workspace.visitPhotos.imageUnavailable";
    return <figure className="visit-photo-shot" key={slot}>
      <div className="visit-photo-shot-stage" data-present={Boolean(src)}>
        {src ? <img src={src} alt={label} onError={() => fail(slot)} />
          : <p role={pair[slot] ? "status" : undefined}>{t(message)}</p>}
      </div>
      <figcaption>
        <strong>{label}</strong>
        {interactive && src && <button type="button" className="pc-button pc-button--secondary" aria-haspopup="dialog"
          onClick={(event) => setPreview({ slot, trigger: event.currentTarget })}>
          {t("workspace.visitPhotos.view", { photo: label })}
        </button>}
        {interactive && onDrawing && pair[slot] && <button type="button" className="pc-button pc-button--secondary"
          aria-expanded={drawingSlot === slot}
          onClick={(event) => onDrawing({ slot, trigger: event.currentTarget })}>
          {t("annotation.visit.open", { photo: label })}
        </button>}
      </figcaption>
    </figure>;
  }

  return (
    <div className="visit-photo-comparison">
      <div className="visit-photo-comparison-header">
        <strong>{t("workspace.visitPhotos.originals")}</strong>
        {ready ? <span className="visit-photo-comparison-badge">{t("workspace.visitPhotos.match")}</span>
          : !complete && <span className="visit-photo-comparison-badge" data-incomplete>{t("workspace.visitPhotos.incomplete")}</span>}
      </div>
      <div className="visit-photo-shots">{slots.map((slot) => shot(slot, true))}</div>
      <div className="visit-photo-comparison-footer">
        {framing && <p className="pc-note">{framing}</p>}
        {ready && <button type="button" className="pc-button pc-button--primary" aria-haspopup="dialog"
          onClick={(event) => setPreview({ slot: "pair", trigger: event.currentTarget })}>
          {t("workspace.visitPhotos.compare")}
        </button>}
      </div>
      {preview && <VisitPhotoPreviewFrame title={title} trigger={preview.trigger}
        comparison={preview.slot === "pair"} onClose={() => setPreview(null)}>
        {singleSlot ? <div className="native-lab-preview-stage">
          {srcFor(singleSlot) ? <img src={srcFor(singleSlot) ?? undefined} alt={title}
            onError={() => fail(singleSlot)} />
            : <p className="native-lab-preview-message" role="status">{t("workspace.visitPhotos.imageUnavailable")}</p>}
        </div> : <div className="visit-photo-comparison-stage">
          <div className="visit-photo-shots">{slots.map((slot) => shot(slot, false))}</div>
          {framing && <p className="pc-note">{framing}</p>}
        </div>}
      </VisitPhotoPreviewFrame>}
    </div>
  );
}

function VisitPhotoDrawing({ appointment, selection, drawing, onClose }: {
  appointment: string; selection: Selection; drawing: DrawingEntry; onClose: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const { slot, trigger } = selection;
  const src = useVisitPhotoSource(appointment, slot);
  useEffect(() => {
    const frame = dialog.current;
    if (!frame) return;
    frame.showModal();
    close.current?.focus();
    return () => {
      if (frame.open) frame.close();
      if (trigger.isConnected) trigger.focus();
    };
  }, [trigger]);

  return <dialog ref={dialog} className="native-lab-preview" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="native-lab-preview-toolbar">
      <div className="native-lab-preview-heading"><h2 id={titleId}>{t("annotation.visit.open", { photo: t(slotKey[slot]) })}</h2></div>
      <button ref={close} type="button" className="pc-button pc-button--ghost" onClick={onClose}>
        {t("annotation.visit.close")}
      </button>
    </div>
    <div className="visit-photo-drawing-body">
      {src ? <Suspense fallback={<p className="pc-note" role="status">{t("common.loading")}</p>}>
        <VisitAnnotationWorkspace imageUrl={src} target={{ appointment, slot, encounter: drawing.encounter }}
          callbacks={drawing.callbacks} writesBlocked={drawing.writesBlocked} />
      </Suspense> : <p className="pc-note" role="status">
        {t(src === undefined ? "common.loading" : "workspace.visitPhotos.imageUnavailable")}
      </p>}
    </div>
  </dialog>;
}

function VisitPhotoPreview({ appointment, selection, onClose }: {
  appointment: string;
  selection: Selection;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { slot, trigger } = selection;
  const src = useVisitPhotoSource(appointment, slot);
  const [imageFailed, setImageFailed] = useState(false);
  const title = t(slotKey[slot]);
  return <VisitPhotoPreviewFrame title={title} trigger={trigger} onClose={onClose}>
    <div className="native-lab-preview-stage">
      {src && !imageFailed ? <img src={src} alt={title} onError={() => setImageFailed(true)} /> : (
        <p className="native-lab-preview-message" role="status">
          {t(!imageFailed && src === undefined ? "common.loading" : "workspace.visitPhotos.imageUnavailable")}
        </p>
      )}
    </div>
  </VisitPhotoPreviewFrame>;
}

function VisitPhotoPreviewFrame({ title, trigger, children, comparison = false, onClose }: {
  title: string; trigger: HTMLButtonElement; children: ReactNode; comparison?: boolean; onClose: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    closeRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      if (trigger.isConnected) trigger.focus();
    };
  }, [trigger]);

  // Reuse the in-place lab preview frame without its report/download actions.
  return (
    <dialog ref={dialogRef} className={comparison ? "native-lab-preview visit-photo-comparison-dialog" : "native-lab-preview"}
      aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="native-lab-preview-toolbar">
        <div className="native-lab-preview-heading"><h2 id={titleId}>{title}</h2></div>
        <button ref={closeRef} type="button" className="pc-button pc-button--ghost" onClick={onClose}>{t("action.close")}</button>
      </div>
      {children}
    </dialog>
  );
}

function useVisitPhotoSource(appointment: string | null, slot: VisitPhotoSlot, ownBefore = false) {
  const [src, setSrc] = useState<string | null>();
  useEffect(() => {
    if (!appointment) return;
    const target = appointment;
    const controller = new AbortController();
    const { signal } = controller;
    let url: string | undefined;
    async function load() {
      await Promise.resolve();
      if (signal.aborted) return;
      try {
        const blob = ownBefore ? await readOwnBeforePhoto(target, signal) : await readVisitPhotoImage(target, slot, signal);
        if (signal.aborted) return;
        if (blob) url = URL.createObjectURL(blob);
        setSrc(url ?? null);
      } catch {
        if (!signal.aborted) setSrc(null);
      }
    }
    void load();
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [appointment, slot, ownBefore]);
  return src;
}
