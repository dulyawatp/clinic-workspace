import { lazy, type ReactNode, Suspense, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import type { VisitAnnotationCallbacks } from "../../transport/native/visit-annotations";
import {
  type VisitPhotoSlot,
  type VisitPhotoTarget,
  readVisitPhotoAppointment,
  readVisitPhotoImage,
  readVisitPhotoPair,
} from "../../transport/native/visit-photos";
import { useVisitPhotoActivity, VisitPhotoCapture } from "./VisitPhotoCapture";

const VisitAnnotationWorkspace = lazy(() => import("../photos/annotate/VisitAnnotationWorkspace")
  .then(({ VisitAnnotationWorkspace }) => ({ default: VisitAnnotationWorkspace })));

type Source = { appointment: string } | { encounterId: string; patientId: string };
const slots = ["Before", "After"] as const;
const slotKey = { Before: "workspace.visitPhotos.before", After: "workspace.visitPhotos.after" } as const;
type Pair = NonNullable<Awaited<ReturnType<typeof readVisitPhotoPair>>>;
type Selection = { slot: VisitPhotoSlot; trigger: HTMLButtonElement };
type Drawing = { callbacks: VisitAnnotationCallbacks; writesBlocked: boolean };
type DrawingEntry = Drawing & { encounter: string };

export function NativeVisitPhotos({ source, staffTarget, drawing }: {
  source: Source; staffTarget?: VisitPhotoTarget; drawing?: Drawing;
}) {
  const { t } = useTranslation();
  const { authGeneration, status, session } = useSession();
  if (status !== "authenticated" || !session) return null;
  const doctor = session.user.roles.includes("DOC");
  const staff = session.mode === "live" && (doctor || session.user.roles.includes("REC"));
  const target = staff && staffTarget && "appointment" in source && source.appointment === staffTarget.appointment
    ? { appointment: staffTarget.appointment, ...(doctor && staffTarget.encounter && { encounter: staffTarget.encounter }) }
    : undefined;
  return <VisitPhotoDisclosure key={JSON.stringify([authGeneration, source, target])} source={source}
    target={target} drawing={target?.encounter ? drawing : undefined} title={t("workspace.visitPhotos.open")} />;
}

function VisitPhotoDisclosure({ source, title, target, drawing }: {
  source: Source; title: string; target?: VisitPhotoTarget; drawing?: Drawing;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="visit-photo-disclosure" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{title}</summary>
      {open && (target ? <StaffVisitPhotoPair target={target} drawing={drawing} /> : <VisitPhotoPair source={source} />)}
    </details>
  );
}

function StaffVisitPhotoPair({ target, drawing }: { target: VisitPhotoTarget; drawing?: Drawing }) {
  const activity = useVisitPhotoActivity(target.appointment);
  return <VisitPhotoPair source={target} revision={activity.data.revision} includeFraming={Boolean(target.encounter)}
    drawing={drawing && target.encounter ? {
      ...drawing, encounter: target.encounter,
      writesBlocked: drawing.writesBlocked || Boolean(activity.data.consent) || activity.data.notice === "consentUnknown",
    } : undefined}
    actions={(pair) => <VisitPhotoCapture target={target} activity={activity} pair={pair} />} />;
}

function VisitPhotoPair({ source, revision = 0, includeFraming = false, actions, drawing }: {
  source: Source;
  revision?: number;
  includeFraming?: boolean;
  actions?: (pair: Pair | null | undefined) => ReactNode;
  drawing?: DrawingEntry;
}) {
  const { t } = useTranslation();
  const [result, setResult] = useState<{ revision: number; match: { appointment: string; pair: Pair } | null }>();
  const [selection, setSelection] = useState<Selection | null>(null);
  const [drawingSelection, setDrawingSelection] = useState<Selection | null>(null);
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
  return (
    <>
      {result === undefined ? <p className="pc-note" role="status">{t("common.loading")}</p>
        : !match ? <p className="pc-note" role="status">{t("workspace.nativePhotosUnavailable")}</p>
          : !match.pair.Before && !match.pair.After ? <p className="pc-note" role="status">{t("workspace.visitPhotos.empty")}</p>
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
                        aria-expanded={drawingSelection?.slot === slot}
                        onClick={(event) => setDrawingSelection({ slot, trigger: event.currentTarget })}>
                        {t("annotation.visit.open", { photo: t(slotKey[slot]) })}
                      </button>}
                    </div>
                  ) : <p className="pc-note">{t("workspace.visitPhotos.emptySlot")}</p>}
                </li>
              ))}
            </ul>}
      {match && selection && <VisitPhotoPreview key={JSON.stringify([match.appointment, selection.slot])}
        appointment={match.appointment} selection={selection} onClose={() => setSelection(null)} />}
      {drawing && drawingSelection && result?.revision === revision && match?.appointment === appointmentId && match.pair.drawingsEnabled &&
        match.pair[drawingSelection.slot] &&
        <VisitPhotoDrawing key={JSON.stringify([revision, appointmentId, drawing.encounter, drawingSelection.slot])}
          appointment={match.appointment} selection={drawingSelection} drawing={drawing}
          onClose={() => setDrawingSelection(null)} />}
      {actions?.(result?.revision === revision ? match?.pair ?? null : undefined)}
    </>
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
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const { slot, trigger } = selection;
  const src = useVisitPhotoSource(appointment, slot);
  const [imageFailed, setImageFailed] = useState(false);
  const title = t(slotKey[slot]);
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
    <dialog ref={dialogRef} className="native-lab-preview" aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="native-lab-preview-toolbar">
        <div className="native-lab-preview-heading"><h2 id={titleId}>{title}</h2></div>
        <button ref={closeRef} type="button" className="pc-button pc-button--ghost" onClick={onClose}>{t("action.close")}</button>
      </div>
      <div className="native-lab-preview-stage">
        {src && !imageFailed ? <img src={src} alt={title} onError={() => setImageFailed(true)} /> : (
          <p className="native-lab-preview-message" role="status">
            {t(!imageFailed && src === undefined ? "common.loading" : "workspace.visitPhotos.imageUnavailable")}
          </p>
        )}
      </div>
    </dialog>
  );
}

function useVisitPhotoSource(appointment: string, slot: VisitPhotoSlot) {
  const [src, setSrc] = useState<string | null>();
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    let url: string | undefined;
    async function load() {
      await Promise.resolve();
      if (signal.aborted) return;
      try {
        const blob = await readVisitPhotoImage(appointment, slot, signal);
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
  }, [appointment, slot]);
  return src;
}
