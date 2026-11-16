import { useState } from "react";
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
import {
  completeNativeProcedure,
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
import { useProcedureWriteLock } from "./useProcedureWriteLock";

function nativeFailureKey(code: string): string {
  switch (code) {
    case "validation":
      return "procedureWrite.invalid";
    case "forbidden":
    case "auth":
      return "procedureWrite.forbidden";
    case "conflict":
      return "procedureWrite.conflict";
    case "uncertain":
      return "procedureWrite.uncertain";
    default:
      return "procedureWrite.locked";
  }
}

/**
 * Native completion of one server-owned procedure target.
 *
 * The workspace procedure id is the only identifier here; the consumables and
 * the stock preview stay read-only (the designed preview is unchanged) because
 * the stock movement belongs to the one server transaction. Completion is
 * confirmation-gated, DOC-only, and locked after one attempt: a second call
 * would post a second Stock Entry, so the server refuses it and this button
 * stays locked until an operator resolves the state.
 */
function NativeProcedureCompletion({ workspaceId }: { workspaceId: string }) {
  const { t } = useTranslation();
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  const isDoctor = roles.includes("DOC");
  const { locked, refreshLock } = useProcedureWriteLock();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const unavailable = completed || locked;

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
      setOpen(false);
    } catch (error) {
      setFailure(
        error instanceof NativeProcedureWriteError
          ? nativeFailureKey(error.code)
          : "procedureWrite.uncertain",
      );
    } finally {
      setSaving(false);
      refreshLock();
    }
  }

  return (
    <div data-surface="DOC-03" data-testid="procedure-native-complete">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        title={t("surface.DOC-03")}
      />
      <div className="pc-page__body">
        <Card
          title={`${workspaceId} · ${t("procedureWrite.title")}`}
          actions={
            completed ? (
              <Badge tone="success">{t("procedureWrite.completed")}</Badge>
            ) : (
              <Badge tone="warning">{t("status.inProgress")}</Badge>
            )
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
            {unavailable && !completed && (
              <Badge tone="danger">{t("procedureWrite.locked")}</Badge>
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
      </div>

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
    if (id && get("write.procedure.complete") === "enabled") {
      return <NativeProcedureCompletion workspaceId={id} />;
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
