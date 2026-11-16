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
import { StatePanel } from "../../ui/StatePanel";
import {
  patientLabel,
  procedureById,
  roomLabel,
  templateName,
} from "./sample-scope";

/**
 * DOC-03 procedure detail.
 *
 * Consumables come from the authored procedure template and are resolved to
 * real sample lots; the preview recomputes `before → after` from the lot
 * quantity instead of copying a total. Completion is a single local sample
 * transition — the screen states that real consumption needs one verified
 * server transaction and offers no transport.
 */
export function ProcedureDetailPage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { id } = useParams();
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  const procedure = id ? procedureById(id) : null;
  const [previewed, setPreviewed] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!procedure) {
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
