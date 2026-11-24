import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { nativeWriteMarkerOwner, useSession } from "../../../app/SessionProvider";
import { Badge, Button } from "../../../design/components";
import type { VisitAnnotationCallbacks, VisitAnnotationRead, VisitAnnotationTarget } from "../../../transport/native/visit-annotations";
import { AnnotationWorkspace } from "./AnnotationWorkspace";
import type { LayerShape } from "./engine";
import { decodeVisitDrawing, encodeVisitDrawing } from "./visit-drawing";

type Notice = "loading" | "unavailable" | "pending" | "uncertain" | "stale" | "rejected"
  | "saved" | "reopenFailed" | "tooLarge" | "readOnly" | null;

export function VisitAnnotationWorkspace({ target, imageUrl, callbacks, writesBlocked = false }: {
  target: VisitAnnotationTarget;
  imageUrl: string;
  callbacks: VisitAnnotationCallbacks;
  writesBlocked?: boolean;
}) {
  const { authGeneration, status } = useSession();
  if (status !== "authenticated") return null;
  return <SlotWorkspace key={JSON.stringify([authGeneration, target.appointment, target.encounter, target.slot])}
    target={target} imageUrl={imageUrl} callbacks={callbacks} writesBlocked={writesBlocked} />;
}

function SlotWorkspace({ target, imageUrl, callbacks, writesBlocked }: {
  target: VisitAnnotationTarget; imageUrl: string; callbacks: VisitAnnotationCallbacks; writesBlocked: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { authGeneration, session } = useSession();
  const owner = nativeWriteMarkerOwner(session);
  const { appointment, encounter, slot } = target;
  const cache = useQueryClient();
  const cacheKey = ["visit-drawing-request", owner ?? authGeneration, appointment, encounter, slot] as const;
  const request = useQuery<string | null>({
    queryKey: cacheKey, enabled: false, initialData: null, gcTime: Infinity, queryFn: () => null,
    ...(owner ? { meta: { nativeWriteMarker: owner } } : {}),
  });
  const [document, setDocument] = useState<VisitAnnotationRead>();
  const [notice, setNotice] = useState<Notice>("loading");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const verifiedVersion = useRef<number | undefined>(undefined);
  const adopted = useRef<{ baseChanged: boolean } | null>(null);
  const slotName = t(slot === "Before" ? "workspace.visitPhotos.before" : "workspace.visitPhotos.after");

  async function readDrawing(signal: AbortSignal, version?: number) {
    const result = await callbacks.read({ appointment, slot, ...(version === undefined ? {} : { version }) }, signal)
      .catch(() => null);
    if (!result || !Number.isSafeInteger(result.version) || result.version < 0 ||
        Boolean(result.annotation) !== (result.version > 0) || (version !== undefined && result.version !== version)) {
      return null;
    }
    return result;
  }

  async function load(signal: AbortSignal, version?: number, afterCommit = false) {
    const result = await readDrawing(signal, version);
    if (signal.aborted) return;
    if (!result) {
      setNotice(afterCommit && !adopted.current ? "reopenFailed" : "unavailable");
      return;
    }
    if (afterCommit) {
      cache.setQueryData(cacheKey, result.pendingRequestKey);
      verifiedVersion.current = undefined;
      if (adopted.current) {
        // The committed operation is not this editor's work. Keep both its
        // geometry and optimistic base until the user explicitly reloads.
        const baseChanged = adopted.current.baseChanged || result.version !== document?.version;
        adopted.current = result.pendingRequestKey ? { baseChanged } : null;
        setDocument((current) => current && { ...current, canWrite: result.canWrite });
        setNotice(result.pendingRequestKey ? "uncertain" : baseChanged ? "stale" : "rejected");
        return;
      }
    } else if (result.pendingRequestKey) {
      cache.setQueryData<string | null>(cacheKey, (key) => key ?? result.pendingRequestKey);
    }
    setDocument(result);
    setRevision((value) => value + 1);
    setNotice(afterCommit ? "saved" : cache.getQueryData(cacheKey) ? "uncertain" : null);
  }

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    // StrictMode discards the first effect before starting a protected request.
    void Promise.resolve().then(() => {
      if (!controller.signal.aborted) return load(controller.signal);
    });
    return () => controller.abort();
  }, []);

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action(signal);
    } finally {
      if (!signal.aborted) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }

  function settle(state: "pending" | "uncertain" | "stale" | "rejected") {
    if (state === "stale" || state === "rejected") {
      cache.setQueryData(cacheKey, null);
      if (adopted.current?.baseChanged) state = "stale";
      adopted.current = null;
    }
    setNotice(state);
  }

  const editable = Boolean(document?.canWrite && !writesBlocked && !busy && !request.data);
  const saveEnabled = editable && notice !== "stale" && notice !== "unavailable";

  function save(shapes: readonly LayerShape[]) {
    if (!document || !saveEnabled || cache.getQueryData(cacheKey)) return;
    const annotation = encodeVisitDrawing(shapes);
    if (!annotation) { setNotice("tooLarge"); return; }
    void run(async (signal) => {
      let request_key: string;
      try { request_key = crypto.randomUUID(); } catch { setNotice("unavailable"); return; }
      cache.setQueryData(cacheKey, request_key);
      const state = await callbacks.submit({ ...target, expected_version: document.version, request_key, annotation }, signal)
        .catch(() => "uncertain" as const);
      if (signal.aborted) return;
      if (state !== "rejected") { settle(state); return; }

      // A definitive rejection releases only the new key. The same draft
      // may already have a separate unresolved intent owned by this author.
      const result = await readDrawing(signal);
      if (signal.aborted || cache.getQueryData(cacheKey) !== request_key) return;
      const key = result?.pendingRequestKey ?? null;
      const baseChanged = Boolean(result && result.version !== document.version);
      adopted.current = key ? { baseChanged } : null;
      cache.setQueryData(cacheKey, key);
      setDocument({ ...document, canWrite: result?.canWrite ?? false });
      setNotice(!result ? "unavailable" : key ? "uncertain" : baseChanged ? "stale" : "rejected");
    });
  }

  function check(request_key: string) {
    void run(async (signal) => {
      const status = await callbacks.status({ appointment, slot, request_key }, signal)
        .catch(() => ({ state: "uncertain" as const }));
      if (signal.aborted) return;
      if (status.state !== "committed") { settle(status.state); return; }
      if (status.request_key !== request_key || !Number.isSafeInteger(status.version) || status.version < 1) {
        setNotice("uncertain");
        return;
      }
      verifiedVersion.current = status.version;
      await load(signal, status.version, true);
    });
  }

  const retry = notice === "unavailable" || notice === "stale" || notice === "reopenFailed";
  const feedback = <>
    {(busy || notice || (document && (!document.canWrite || writesBlocked))) && <p className="annotation-status" role="status">
      {busy || notice === "loading" ? t("common.loading")
        : notice === "saved" ? t("annotation.saved")
          : t(`annotation.visit.${notice ?? "readOnly"}`)}
    </p>}
    {request.data && !verifiedVersion.current &&
      <Button size="sm" onClick={() => check(request.data!)} disabled={busy || notice === "loading"}>
        {t("annotation.visit.check")}
      </Button>}
    {retry && <Button size="sm" disabled={busy} onClick={() => {
      void run((signal) => load(signal, verifiedVersion.current, verifiedVersion.current !== undefined));
    }}>
      {t(notice === "stale" ? "annotation.visit.loadLatest" : "annotation.visit.load")}
    </Button>}
  </>;
  if (!document) return feedback;

  return <AnnotationWorkspace key={revision} patientId={id} live={{
    frame: { id: slot, url: imageUrl, name: slotName, category: slot.toLowerCase() },
    shapes: document.annotation ? decodeVisitDrawing(document.annotation) : [],
    editable, saveEnabled, onSave: save, feedback,
    version: document.version > 0 ? <li className="annotation-layer">
      <Badge tone="success">{t("annotation.state.committed")}</Badge>{" "}
      {t("annotation.version", { number: document.version })}{" "}
      <Button size="sm" disabled={busy || Boolean(request.data) || notice === "stale" || notice === "unavailable"}
        onClick={() => void run((signal) => load(signal, document.version))}>
        {t("annotation.openVersion")}
      </Button>
    </li> : undefined,
  }} />;
}
