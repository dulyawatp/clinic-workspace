import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  PageHeader,
  SampleBanner,
  UnavailablePanel,
} from "../../design/components";
import { opsData } from "../../fixtures/data-ops";
import { translateClinicalText } from "../../i18n";
import { formatDate, formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { findProcedures, ProcedureReadError, viewProcedure, type ProcedureStatus } from "../../transport/native/procedure-read";
import {
  completeNativeProcedure,
  createNativeProcedure,
  NativeProcedureWriteError,
} from "../../transport/native/procedure-write";
import { useCapabilities } from "../shell/CapabilityProvider";
import { Button as GateButton } from "../../ui/Button";
import { WriteGate } from "../../ui/WriteGate";
import { StatePanel } from "../../ui/StatePanel";
import {
  patientLabel,
  procedureById,
  roomLabel,
  templateName,
} from "./sample-scope";

function createFailureKey(code: string): string {
  switch (code) {
    case "validation":
      return "procedureCreate.invalid";
    case "forbidden":
    case "auth":
      return "procedureCreate.forbidden";
    case "conflict":
      return "procedureCreate.conflict";
    default:
      return "procedureCreate.uncertain";
  }
}

function nativeFailureKey(code: string): string {
  switch (code) {
    case "validation":
      return "procedureWrite.invalid";
    case "forbidden":
    case "auth":
      return "procedureWrite.forbidden";
    case "conflict":
      return "procedureWrite.conflict";
    default:
      return "procedureWrite.uncertain";
  }
}

/**
 * Native creation of one Clinical Procedure from a submitted Service Request.
 *
 * The actor types the Service Request they were told to use (the encounter
 * cascade creates it); the server reads every procedure field from that request
 * and returns the native docname it created. One request per submit and no
 * automatic retry: a definite refusal leaves the form usable for a corrected
 * attempt, while an uncertain outcome stops the form for this screen.
 */
function NativeProcedureCreation() {
  const { t } = useTranslation();
  const [serviceRequest, setServiceRequest] = useState("");
  const [saving, setSaving] = useState(false);
  const [halted, setHalted] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [results, setResults] = useState<ProcedureStatus[] | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);

  async function lookup() {
    setLooking(true);
    setResults(null);
    setLookupError(null);
    try {
      setResults(await findProcedures(serviceRequest.trim()));
    } catch (error) {
      setLookupError(error instanceof ProcedureReadError && error.kind === "permission" ? "procedureRead.permission" : "procedureRead.unavailable");
    } finally {
      setLooking(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || halted) return;
    const request = serviceRequest.trim();
    if (!request) {
      setFailure("procedureCreate.invalid");
      return;
    }
    setFailure(null);
    setSaving(true);
    try {
      const result = await createNativeProcedure({
        operation_key: crypto.randomUUID(),
        service_request: request,
      });
      setCreated(result.procedure.procedure_id);
      setServiceRequest("");
    } catch (error) {
      const code =
        error instanceof NativeProcedureWriteError ? error.code : "uncertain";
      setFailure(createFailureKey(code));
      // A pre-mutation refusal is a known outcome; an unknown one stops here.
      if (code === "uncertain") setHalted(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={t("procedureCreate.title")}>
      <p className="pc-note">{t("procedureCreate.notice")}</p>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <div className="admin-write-field">
          <label htmlFor="procedure-create-request">
            {t("procedureCreate.serviceRequest")}
          </label>
          <input
            id="procedure-create-request"
            maxLength={140}
            value={serviceRequest}
            required
            readOnly={saving || halted}
            aria-describedby={halted ? "procedure-lookup-notice" : undefined}
            onChange={(event) => setServiceRequest(event.target.value)}
          />
        </div>
        <div className="pc-row">
          <GateButton
            type="submit"
            variant="primary"
            disabled={saving || halted}
          >
            {saving ? t("procedureCreate.saving") : t("procedureCreate.action")}
          </GateButton>
        </div>
      </form>
      {created && (
        <p className="pc-status-line" data-testid="procedure-created">
          {t("procedureCreate.created")}{" "}
          <Link className="ops-mono" to={ROUTES.procedure(created)}>{created}</Link>
        </p>
      )}
      {failure && (
        <p className="pc-error" role="alert">
          {t(failure)}
        </p>
      )}
      {halted && (
        <div className="pc-stack">
          <p id="procedure-lookup-notice" className="pc-note">{t("procedureRead.lookupNotice")}</p>
          <GateButton type="button" onClick={() => void lookup()} disabled={looking}>
            {looking ? t("procedureRead.loading") : t("procedureRead.lookup")}
          </GateButton>
          <div aria-live="polite" aria-atomic="true">
            {looking && <p>{t("procedureRead.loading")}</p>}
            {lookupError && <p className="pc-error">{t(lookupError)}</p>}
            {results && (results.length ? (
              <ul>
                {results.map((item) => <li key={item.procedure_id}>
                  <Link to={ROUTES.procedure(item.procedure_id)}>{item.procedure_id}</Link>
                  {" · "}{item.status}{" · "}{t("procedureRead.docstatus")}: {item.docstatus}
                </li>)}
              </ul>
            ) : <p>{t("procedureRead.noResults")}</p>)}
          </div>
        </div>
      )}
    </Card>
  );
}

/**
 * Native completion of one server-owned procedure target.
 *
 * The workspace procedure id or native docname is the only identifier here; the
 * consumables and the stock preview stay read-only (the designed preview is
 * unchanged) because the stock movement belongs to the one server transaction.
 * Completion is confirmation-gated and DOC-only. One request per submit and no
 * automatic retry: a definite refusal leaves the action available for an
 * explicit retry, while an uncertain outcome stops it for this screen. There is
 * a separate card reports status only after a successful native read.
 */
function NativeProcedureCompletionCard({ workspaceId, onCompleted }: { workspaceId: string; onCompleted: () => void }) {
  const { t } = useTranslation();
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  const isDoctor = roles.includes("DOC");
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [halted, setHalted] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const unavailable = completed || halted;

  async function submit() {
    if (saving || unavailable) return;
    setSaving(true);
    setFailure(null);
    try {
      await completeNativeProcedure({
        operation_key: crypto.randomUUID(),
        procedure: workspaceId,
      });
      setCompleted(true);
      onCompleted();
      setOpen(false);
    } catch (error) {
      const code =
        error instanceof NativeProcedureWriteError ? error.code : "uncertain";
      setFailure(nativeFailureKey(code));
      // A pre-mutation refusal is a known outcome; an unknown one stops here.
      if (code === "uncertain") setHalted(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-testid="procedure-native-complete">
      <Card
        title={`${workspaceId} · ${t("procedureWrite.title")}`}
        actions={
          completed ? (
            <Badge tone="success">{t("procedureWrite.completed")}</Badge>
          ) : null
        }
      >
        <p className="pc-note">{t("procedureWrite.notice")}</p>
        <p className="pc-note" data-testid="procedure-write-stock-note">
          {t("procedureWrite.stockNote")}
        </p>
        <div className="pc-row">
          {isDoctor ? (
            <WriteGate capability="write.procedure.complete">
              <GateButton
                variant="primary"
                onClick={() => setOpen(true)}
                disabled={unavailable}
              >
                {t("procedureWrite.completeAction")}
              </GateButton>
            </WriteGate>
          ) : (
            <p className="pc-note">{t("procedureWrite.doctorOnly")}</p>
          )}
        </div>
        {completed && (
          <p className="pc-status-line">{t("procedureWrite.completed")}</p>
        )}
        {failure && (
          <p className="pc-error" role="alert">
            {t(failure)}
          </p>
        )}
      </Card>

      {open && (
        <Drawer
          title={t("procedureWrite.completeAction")}
          onClose={() => setOpen(false)}
          footer={
            <div className="pc-row">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("action.close")}
              </Button>
              <Button
                variant="primary"
                onClick={submit}
                disabled={saving || unavailable}
              >
                {t("action.confirm")}
              </Button>
            </div>
          }
        >
          <div className="pc-stack">
            <p>{t("procedureWrite.confirmBody", { procedure: workspaceId })}</p>
            <p className="pc-note">{t("procedureWrite.stockNote")}</p>
          </div>
        </Drawer>
      )}
    </div>
  );
}

function NativeProcedureStatusCard({ workspaceId }: { workspaceId: string }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ProcedureStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setStatus(null);
    setError(null);
    void viewProcedure(workspaceId).then((result) => {
      if (active) setStatus(result);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof ProcedureReadError && reason.kind === "permission" ? "procedureRead.permission" : "procedureRead.unavailable");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [workspaceId, revision]);

  return <Card title={t("procedureRead.title")}>
    {loading && <p role="status">{t("procedureRead.loading")}</p>}
    {error && <p role="alert" className="pc-error">{t(error)}</p>}
    {status && <div role="status">
      <p>{t("procedureRead.identifier")}: <span className="ops-mono">{status.procedure_id}</span></p>
      <p>{t("procedureRead.status")}: {status.status}</p>
      <p>{t("procedureRead.docstatus")}: {status.docstatus}</p>
    </div>}
    <GateButton type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}>{t("procedureRead.refresh")}</GateButton>
  </Card>;
}

/**
 * The native DOC-03 surface: one page header and the two write cards the
 * deployment advertises (create the procedure, complete it).
 */
function NativeProcedureSurface({
  workspaceId,
  create,
  complete,
}: {
  workspaceId: string;
  create: boolean;
  complete: boolean;
}) {
  const { t } = useTranslation();
  const [statusRevision, setStatusRevision] = useState(0);

  return (
    <div data-surface="DOC-03" data-testid="procedure-native">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        title={t("surface.DOC-03")}
      />
      <div className="pc-page__body">
        {nativeTransportEnabled() && <NativeProcedureStatusCard key={statusRevision} workspaceId={workspaceId} />}
        {create && <NativeProcedureCreation />}
        {complete && (
          <NativeProcedureCompletionCard workspaceId={workspaceId} onCompleted={() => setStatusRevision((value) => value + 1)} />
        )}
      </div>
    </div>
  );
}

/**
 * DOC-03 procedure detail.
 *
 * Consumables come from the authored procedure template and are resolved to
 * real sample lots; the preview recomputes `before → after` from the lot
 * quantity instead of copying a total. For the sample rows completion is a
 * single local sample transition. A workspace procedure id that is not an
 * authored row is the native surface: it renders the same designed page only
 * when the deployment advertises the procedure write capability, and its
 * completion is one confirmed server transaction.
 */
export function ProcedureDetailPage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { id } = useParams();
  const { session } = useSession();
  const { get } = useCapabilities();
  const roles = session?.user.roles ?? [];
  const procedure = id ? procedureById(id) : null;
  const [previewed, setPreviewed] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!procedure) {
    const canComplete = get("write.procedure.complete") === "enabled";
    const canCreate = get("write.procedure.create") === "enabled";
    if (id && (canComplete || canCreate || (nativeTransportEnabled() && (roles.includes("DOC") || roles.includes("NUR"))))) {
      return (
        <NativeProcedureSurface
          workspaceId={id}
          create={canCreate}
          complete={canComplete}
        />
      );
    }
    return (
      <div data-surface="DOC-03" data-testid="procedure-detail">
        <PageHeader title={t("surface.DOC-03")} />
        <div className="shell-page-body">
          <StatePanel kind="empty" title={t("procedure.notFound")} />
        </div>
      </div>
    );
  }

  // A completed sample procedure cannot be consumed again; only an in-progress
  // row is startable, and the completed label below reflects that state.
  const alreadyCompleted = completed || procedure.status === "completed";
  const startable =
    procedure.status !== "scheduled" && procedure.status !== "completed";
  const patient = patientLabel(procedure.patientId, locale);
  const stockLines = procedure.consumables.map((line) => {
    const after = line.batch.qty - line.qty;
    return {
      line,
      after,
      expired: opsData.batchStatus(line.batch) === "expired",
    };
  });
  // An empty template or an exhausted/expired lot cannot be consumed, so the
  // confirmation stays unavailable instead of simulating a negative movement.
  const blocked =
    procedure.consumables.length === 0 ||
    stockLines.some((entry) => entry.expired || entry.after < 0);
  const canConfirm =
    roles.includes("DOC") && startable && !completed && !blocked;

  const confirm = () => {
    if (!startable) {
      setError(t("procedure.notStarted"));
      setDialog(false);
      return;
    }
    if (!previewed) {
      setError(t("procedure.previewRequired"));
      setDialog(false);
      return;
    }
    if (blocked) {
      setError(t("procedure.expired"));
      setDialog(false);
      return;
    }
    setError(null);
    setCompleted(true);
    setDialog(false);
  };

  return (
    <div data-surface="DOC-03" data-testid="procedure-detail">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("surface.DOC-03")}
      />
      <div className="pc-page__body">
        <SampleBanner label={t("app.demoTag")} title={t("procedure.banner")} />

        <Card
          title={`${procedure.id} · ${templateName(procedure.templateId, locale)}`}
          actions={
            <Badge
              tone={
                alreadyCompleted ? "success" : startable ? "warning" : "info"
              }
            >
              {t(
                alreadyCompleted
                  ? "procedure.completed"
                  : startable
                    ? "status.inProgress"
                    : "procedure.scheduled",
              )}
            </Badge>
          }
        >
          <dl className="pc-dl">
            <div>
              <dt>{t("procedure.patient")}</dt>
              <dd>
                {patient} · {procedure.patientId}
              </dd>
            </div>
            <div>
              <dt>{t("procedure.visit")}</dt>
              <dd>{procedure.encounterId ?? t("lab.notRecorded")}</dd>
            </div>
            <div>
              <dt>{t("procedure.appointment")}</dt>
              <dd>{procedure.appointmentId ?? t("lab.notRecorded")}</dd>
            </div>
            <div>
              <dt>{t("procedure.scheduledAt")}</dt>
              <dd>{formatDateTime(procedure.at, locale)}</dd>
            </div>
            <div>
              <dt>{t("procedure.room")}</dt>
              <dd>
                {procedure.room
                  ? `${roomLabel(procedure.room, locale)} · ${procedure.room}`
                  : t("clinical.sourceUnavailable")}
              </dd>
            </div>
          </dl>
        </Card>

        <Card title={t("procedure.consumables")} flush>
          <DataTable
            caption={t("procedure.consumables")}
            columns={[
              { key: "item", label: t("procedure.item") },
              { key: "qty", label: t("procedure.qty"), align: "end" },
              { key: "batch", label: t("procedure.batch") },
              { key: "warehouse", label: t("procedure.warehouse") },
              { key: "expiry", label: t("procedure.expiry") },
              { key: "state", label: t("procedure.batchState") },
              { key: "onHand", label: t("procedure.onHand"), align: "end" },
            ]}
            rows={stockLines.map(({ line, expired }) => [
              line.itemName
                ? translateClinicalText(line.itemName[locale])
                : line.itemId,
              `${line.qty} ${line.uom}`,
              line.batch.batchId,
              line.batch.warehouse,
              formatDate(line.batch.expiry, locale),
              <Badge key="state" tone={expired ? "danger" : "success"}>
                {t(expired ? "status.expired" : "status.usable")}
              </Badge>,
              line.batch.qty,
            ])}
            empty={t("procedure.noConsumables")}
          />
        </Card>

        <Card title={t("procedure.stockPreview")}>
          {!previewed ? (
            <p className="pc-note">{t("procedure.previewRequired")}</p>
          ) : (
            <div className="pc-stack" data-testid="procedure-stock-preview">
              {stockLines.map(({ line, after }) => (
                <p className="pc-status-line" key={line.lineIndex}>
                  {t("procedure.stockLine", {
                    warehouse: line.batch.warehouse,
                    batch: line.batch.batchId,
                    before: line.batch.qty,
                    after,
                  })}
                </p>
              ))}
              {blocked && <p className="pc-error">{t("procedure.expired")}</p>}
            </div>
          )}
          <div className="pc-row">
            <Button variant="primary" onClick={() => setPreviewed(true)}>
              {t("procedure.previewAction")}
            </Button>
            <Button
              variant="secondary"
              onClick={() => setDialog(true)}
              disabled={!canConfirm}
            >
              {t("procedure.completeAction")}
            </Button>
            {alreadyCompleted && (
              <Badge tone="success">{t("procedure.completed")}</Badge>
            )}
          </div>
          {error && (
            <p className="pc-error" role="alert">
              {error}
            </p>
          )}
          {procedure.status === "scheduled" && (
            <p className="pc-note">{t("procedure.notStarted")}</p>
          )}
          <p className="pc-note">{t("procedure.completeOnce")}</p>
        </Card>

        <UnavailablePanel
          title={t("procedure.noTransport")}
          detail={t("visit.proceduresNotice")}
        />
      </div>

      {dialog && (
        <Drawer
          title={t("procedure.completeAction")}
          onClose={() => setDialog(false)}
          footer={
            <div className="pc-row">
              <Button variant="ghost" onClick={() => setDialog(false)}>
                {t("action.close")}
              </Button>
              <Button variant="primary" onClick={confirm} disabled={!previewed}>
                {t("action.confirm")}
              </Button>
            </div>
          }
        >
          <div className="pc-stack">
            <p>{t("procedure.completeOnce")}</p>
            <p className="pc-note">{t("procedure.noTransport")}</p>
          </div>
        </Drawer>
      )}
    </div>
  );
}
