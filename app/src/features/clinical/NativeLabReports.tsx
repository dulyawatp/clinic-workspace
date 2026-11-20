import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../design/components";
import { useLocale } from "../../i18n/LocaleProvider";
import type { EncounterReadError } from "../../transport/native/encounter-read";
import {
  isLabReportFile,
  type LabReport,
  type LabReportContext,
  type LabReportPage,
  listLabReports,
  readLabReportPreview,
  uploadLabReport,
} from "../../transport/native/lab-reports";

type UploadLock = "ready" | "uncertain" | "unavailable";

function uploadKey(ref: string): string {
  let fingerprint = 0xcbf29ce484222325n;
  for (let i = 0; i < ref.length; i++) {
    fingerprint = BigInt.asUintN(64, (fingerprint ^ BigInt(ref.charCodeAt(i))) * 0x100000001b3n);
  }
  return `clinic.lab.upload.${fingerprint.toString(16)}`;
}

function readLock(key: string): UploadLock {
  try {
    const state = sessionStorage.getItem(key);
    return state === "pending" ? "uncertain" : state === null ? "ready" : "unavailable";
  } catch {
    return "unavailable";
  }
}

function clearLock(key: string) {
  try { sessionStorage.removeItem(key); }
  catch { /* Leave the conservative same-tab lock. */ }
}

function uploadError(error: unknown): string | null {
  const code = (error as EncounterReadError | undefined)?.code;
  if (code === "FORBIDDEN" || code === "AUTH_EXPIRED") return "labReports.uploadDenied";
  if (code === "FILE_TOO_LARGE") return "labReports.tooLarge";
  if (code === "VALIDATION" || code === "UNSUPPORTED") return "labReports.rejected";
  return null;
}

function AppointmentReportUpload({ appointmentRef, onRefresh }: { appointmentRef: string; onRefresh: () => void }) {
  const { t } = useTranslation();
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const key = uploadKey(appointmentRef);
  const [state, setState] = useState(() => readLock(key));
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || state !== "ready" || inFlight.current) return;
    inFlight.current = true;
    const current = readLock(key);
    if (current !== "ready") {
      setState(current);
      inFlight.current = false;
      return;
    }
    try {
      // Keep only a marker across remount, never file metadata/content.
      sessionStorage.setItem(key, "pending");
      if (readLock(key) !== "uncertain") throw new Error();
    } catch {
      clearLock(key);
      setState("unavailable");
      inFlight.current = false;
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      await uploadLabReport(appointmentRef, file);
      clearLock(key);
      if (!mounted.current) return;
      setFile(null);
      if (input.current) input.current.value = "";
      setFeedback("labReports.uploaded");
      onRefresh();
    } catch (error) {
      const refusal = uploadError(error);
      if (refusal) clearLock(key);
      if (!mounted.current) return;
      if (refusal) setFeedback(refusal);
      else {
        setFile(null);
        setState("uncertain");
        onRefresh();
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  if (state !== "ready") {
    return <p role="alert">{t(state === "uncertain" ? "labReports.uncertain" : "labReports.recoveryUnavailable")}</p>;
  }
  return (
    <form className="visit-preview-stack native-lab-upload" onSubmit={(event) => void upload(event)}>
      <div className="pc-field">
        <label htmlFor={inputId}>{t("labReports.file")}</label>
        <input id={inputId} ref={input} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" disabled={busy}
          aria-describedby={`${inputId}-formats`} onChange={(event) => {
            const files = event.currentTarget.files;
            const selected = files?.length === 1 ? files[0] : null;
            if (selected && isLabReportFile(selected)) {
              setFile(selected);
              setFeedback(null);
            } else {
              setFile(null);
              setFeedback(files?.length ? "labReports.invalid" : null);
              event.currentTarget.value = "";
            }
          }} />
      </div>
      <p className="pc-note" id={`${inputId}-formats`}>{t("labReports.formats")}</p>
      <div><Button type="submit" variant="primary" disabled={!file || busy}>
        {t(busy ? "labReports.uploading" : "labReports.upload")}
      </Button></div>
      {feedback && <p role={feedback === "labReports.uploaded" ? "status" : "alert"}>{t(feedback)}</p>}
    </form>
  );
}

function ReportDownload({ report }: { report: LabReport }) {
  const { t } = useTranslation();
  return <a className="pc-button pc-button--secondary" href={report.downloadUrl} download={report.name}
    aria-label={t("labReports.downloadNamed", { name: report.name })}>{t("labReports.download")}</a>;
}

type Preview = { state: "loading" | "unavailable" | "denied" } | { state: "ready"; url: string; pdf: boolean };

function ReportPreview({ report, trigger, onClose }: { report: LabReport; trigger: HTMLButtonElement; onClose: () => void }) {
  const { t } = useTranslation();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [preview, setPreview] = useState<Preview>({ state: "loading" });

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

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    let url: string | undefined;
    async function load() {
      // Let StrictMode's discarded effect cancel before issuing an authorized read.
      await Promise.resolve();
      if (signal.aborted) return;
      try {
        const result = await readLabReportPreview(report, signal);
        if (signal.aborted) return;
        if (result.state !== "ready") {
          setPreview({ state: result.state });
          return;
        }
        const pdf = result.mime === "application/pdf";
        if (pdf && navigator.pdfViewerEnabled !== true) {
          setPreview({ state: "unavailable" });
          return;
        }
        url = URL.createObjectURL(result.blob);
        setPreview({ state: "ready", url, pdf });
      } catch {
        if (!signal.aborted) setPreview({ state: "unavailable" });
      }
    }
    void load();
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [report]);

  const name = t("labReports.previewNamed", { name: report.name });
  const unavailable = () => setPreview({ state: "unavailable" });
  return (
    <dialog ref={dialogRef} className="native-lab-preview" aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="native-lab-preview-toolbar">
        <div className="native-lab-preview-heading">
          <span className="pc-note">{t("labReports.previewTitle")}</span>
          <h2 id={titleId}>{report.name}</h2>
        </div>
        <div className="pc-row native-lab-actions">
          <ReportDownload report={report} />
          <button ref={closeRef} type="button" className="pc-button pc-button--ghost" onClick={onClose}>{t("action.close")}</button>
        </div>
      </div>
      <div className="native-lab-preview-stage">
        {preview.state === "ready" ? (preview.pdf ? (
          // Chromium cannot run its built-in PDF viewer in a sandboxed frame.
          // Only a typed, signature-checked local PDF blob reaches this frame.
          <iframe src={preview.url} title={name} onError={unavailable} />
        ) : (
          <img src={preview.url} alt={name} onError={unavailable} />
        )) : (
          <div className="native-lab-preview-message" role={preview.state === "denied" ? "alert" : "status"}>
            {t(preview.state === "loading" ? "labReports.previewLoading" :
              preview.state === "denied" ? "labReports.previewDenied" : "labReports.previewUnavailable")}
            {preview.state === "unavailable" && <p className="pc-note">{t("labReports.previewDownloadHint")}</p>}
          </div>
        )}
      </div>
    </dialog>
  );
}

function ReportList({ reports }: { reports: LabReport[] }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const size = new Intl.NumberFormat(locale);
  const [selected, setSelected] = useState<{ report: LabReport; trigger: HTMLButtonElement } | null>(null);
  return (
    <>
      <ul className="visit-preview-stack native-lab-list">
        {reports.map((report) => (
          <li className="visit-preview-stack" key={report.id}>
            <strong>{report.name}</strong>
            <dl className="native-lab-facts">
              <div><dt>{t("labReports.size")}</dt><dd>{t("labReports.bytes", { size: size.format(report.sizeBytes) })}</dd></div>
              <div><dt>{t("labReports.by")}</dt><dd>{report.uploadedBy}</dd></div>
              <div><dt>{t("labReports.at")}</dt><dd><time dateTime={report.uploadedAt.replace(" ", "T")}>{report.uploadedAt}</time></dd></div>
            </dl>
            <div className="pc-row native-lab-actions">
              <Button aria-haspopup="dialog" aria-label={t("labReports.viewNamed", { name: report.name })}
                onClick={(event) => setSelected({ report, trigger: event.currentTarget })}>{t("labReports.view")}</Button>
              <ReportDownload report={report} />
            </div>
          </li>
        ))}
      </ul>
      {selected && <ReportPreview {...selected} onClose={() => setSelected(null)} />}
    </>
  );
}

export function NativeLabReportPanel({ context, uploadAppointmentRef }: {
  context: LabReportContext;
  uploadAppointmentRef?: string;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const key = ["native-lab-reports", authGeneration, context.kind, context.id] as const;
  const query = useInfiniteQuery<LabReportPage, EncounterReadError, InfiniteData<LabReportPage>, typeof key, number>({
    queryKey: key,
    queryFn: ({ pageParam, signal }) => listLabReports(context, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: ({ nextOffset }) => nextOffset ?? undefined,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const reports = [...new Map(query.data?.pages.flatMap(({ items }) => items).map((item) => [item.id, item])).values()];
  const refresh = () => { void query.refetch(); };
  const denied = query.error?.code === "FORBIDDEN" || query.error?.code === "AUTH_EXPIRED";
  return (
    <div className="visit-preview-stack native-lab-panel">
      {query.isError ? (
        <p role="alert">{t(denied ? "labReports.denied" : "labReports.unavailable")}</p>
      ) : query.isPending || !query.isFetchedAfterMount ? (
        <p role="status">{t("common.loading")}</p>
      ) : reports.length ? (
        <ReportList key={JSON.stringify(key)} reports={reports} />
      ) : (
        <p className="pc-note">{t("labReports.empty")}</p>
      )}
      <div className="pc-row">
        {query.hasNextPage && !query.isError && <Button disabled={query.isFetching}
          onClick={() => void query.fetchNextPage()}>{t("labReports.more")}</Button>}
        <Button disabled={query.isFetching} onClick={refresh}>{t("labReports.refresh")}</Button>
      </div>
      {uploadAppointmentRef && (
        <AppointmentReportUpload key={JSON.stringify([authGeneration, uploadAppointmentRef])}
          appointmentRef={uploadAppointmentRef} onRefresh={refresh} />
      )}
    </div>
  );
}

export function LabReportDisclosure({ encounterId }: { encounterId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <details className="native-lab-disclosure" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t("labReports.title")}</summary>
      {open && <NativeLabReportPanel context={{ kind: "encounter", id: encounterId }} />}
    </details>
  );
}
