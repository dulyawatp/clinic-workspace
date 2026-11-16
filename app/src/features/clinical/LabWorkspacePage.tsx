import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";
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
} from "../../design/components";
import { translateClinicalText } from "../../i18n";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { StatePanel } from "../../ui/StatePanel";
import { LabStatusBadge } from "./LabPanels";
import {
  LAB_STATUSES,
  type LabRequestView,
  labLinkVerified,
  labRequestById,
  labRequests,
  labStatusKey,
  patientLabel,
  practitionerLabel,
} from "./sample-scope";

type LabDrawer = "send" | "receive" | "review";

const STATUS_FILTERS = ["all", ...LAB_STATUSES] as const;

function statusOf(value: string | null): (typeof STATUS_FILTERS)[number] {
  return STATUS_FILTERS.find((status) => status === value) ?? "all";
}

/**
 * DOC-04 external lab workspace.
 *
 * Six designed display states, the 18-field request/result panel and the
 * upload/review states render over the sample lab rows. Send, receive/upload
 * and review are presented as designed controls in an explicit unavailable
 * state: there is no lab transport in this build.
 */
export function LabWorkspacePage() {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const filter = statusOf(params.get("status"));
  const [drawer, setDrawer] = useState<LabDrawer | null>(null);
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  const isDoctor = roles.includes("DOC");
  const isNurse = roles.includes("NUR");

  const selected = id ? labRequestById(id) : null;
  if (id && !selected) {
    return (
      <div data-surface="DOC-04" data-testid="lab-workspace">
        <PageHeader title={t("surface.DOC-04")} />
        <div className="shell-page-body">
          <StatePanel kind="empty" title={t("lab.notFound")} />
        </div>
      </div>
    );
  }

  const rows = labRequests.filter(
    (row) => filter === "all" || row.status === filter,
  );
  const canSend =
    isNurse && selected?.status === "ordered" && labLinkVerified(selected);
  const canReceive =
    isNurse &&
    selected !== null &&
    ["ordered", "sent", "received"].includes(selected.status) &&
    labLinkVerified(selected);
  const canReview = isDoctor && selected?.status === "received";

  return (
    <div data-surface="DOC-04" data-testid="lab-workspace">
      <PageHeader
        breadcrumb={<Link to={ROUTES.home}>{t("action.goHome")}</Link>}
        eyebrow={`${t("app.name")} · ${t("app.demoTag")}`}
        title={t("surface.DOC-04")}
      />
      <div className="pc-page__body">
        <SampleBanner
          label={t("app.demoTag")}
          title={t("lab.sampleRecordNote")}
        >
          <p className="pc-note">{t("lab.stateNote")}</p>
        </SampleBanner>

        {selected && (
          <LabDetail
            row={selected}
            isDoctor={isDoctor}
            isNurse={isNurse}
            canSend={canSend}
            canReceive={canReceive}
            canReview={canReview}
            onOpenDrawer={setDrawer}
          />
        )}

        <Card title={t("lab.worklist")} hint={t("lab.sampleRecordNote")} flush>
          <div className="lab-toolbar">
            <label className="pc-field__label" htmlFor="labStatusFilter">
              {t("lab.filter")}
            </label>
            <select
              id="labStatusFilter"
              value={filter}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                if (event.target.value === "all") next.delete("status");
                else next.set("status", event.target.value);
                setParams(next, { replace: true });
              }}
            >
              <option value="all">{t("lab.all")}</option>
              {LAB_STATUSES.map((status) => (
                <option value={status} key={status}>
                  {t(labStatusKey(status))}
                </option>
              ))}
            </select>
          </div>
          <DataTable
            caption={t("lab.worklist")}
            columns={[
              { key: "request", label: t("lab.fieldRequest") },
              { key: "test", label: t("lab.fieldTest") },
              { key: "provider", label: t("lab.provider") },
              { key: "status", label: t("clinical.state") },
              { key: "open", label: "" },
            ]}
            rows={rows.map((row) => [
              <span key="request">
                {row.id}
                <br />
                <span className="pc-note">
                  {patientLabel(row.patientId, locale)}
                </span>
              </span>,
              translateClinicalText(row.test[locale]),
              row.provider,
              <LabStatusBadge key="status" status={row.status} />,
              <Link
                key="open"
                className="pc-button pc-button--secondary pc-button--sm"
                to={ROUTES.lab(row.id)}
              >
                {t("lab.openRequest")}
              </Link>,
            ])}
            empty={t("lab.noMatching")}
          />
        </Card>
      </div>

      {selected && drawer === "send" && (
        <LabDrawer
          mode="send"
          row={selected}
          note={t("lab.sendUnavailable")}
          onClose={() => setDrawer(null)}
          unavailableLabel={t("lab.unavailableAction")}
        />
      )}
      {selected && drawer === "receive" && (
        <LabDrawer
          mode="receive"
          row={selected}
          note={t("lab.uploadUnavailable")}
          onClose={() => setDrawer(null)}
          unavailableLabel={t("lab.unavailableAction")}
        />
      )}
      {selected && drawer === "review" && (
        <LabDrawer
          mode="review"
          row={selected}
          note={t("lab.reviewUnavailable")}
          onClose={() => setDrawer(null)}
          unavailableLabel={t("lab.unavailableAction")}
        />
      )}
    </div>
  );
}

function LabDetail({
  row,
  isDoctor,
  isNurse,
  canSend,
  canReceive,
  canReview,
  onOpenDrawer,
}: {
  row: LabRequestView;
  isDoctor: boolean;
  isNurse: boolean;
  canSend: boolean;
  canReceive: boolean;
  canReview: boolean;
  onOpenDrawer: (drawer: LabDrawer) => void;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const linked = labLinkVerified(row);
  const fileNames = row.files.map((file) => file.name);
  // The field is "Uploaded by", so only a real file uploader may fill it. A
  // receiver is different provenance and stays a separate, explicit note.
  const uploadedBy = row.files[0]?.uploadedBy ?? null;
  const receivedBy = row.receivedBy;

  return (
    <Card
      title={`${row.id} · ${translateClinicalText(row.test[locale])}`}
      actions={<LabStatusBadge status={row.status} />}
    >
      <div className="pc-stack">
        {row.status === "collected" && (
          <p className="pc-banner pc-banner--demo" data-testid="lab-collected">
            <span className="pc-banner__body">
              {t("lab.state.collected")} ·{" "}
              {row.collectedAt
                ? formatDateTime(row.collectedAt, locale)
                : t("lab.notRecorded")}{" "}
              · {row.collectedBy ?? t("lab.notRecorded")}
            </span>
          </p>
        )}
        {row.status === "cancelled" && (
          <p
            className="pc-banner pc-banner--danger"
            data-testid="lab-cancelled"
          >
            <span className="pc-banner__body">
              {t("lab.state.cancelled")} ·{" "}
              {row.cancelledAt
                ? formatDateTime(row.cancelledAt, locale)
                : t("lab.notRecorded")}{" "}
              · {row.cancellationReason?.[locale] ?? t("lab.cancelReason")}
            </span>
          </p>
        )}

        <dl className="pc-lab-fields" data-lab-fields="18">
          <dt>{t("lab.fieldPatient")}</dt>
          <dd>
            {patientLabel(row.patientId, locale)} · {row.patientId}
          </dd>
          <dt>{t("lab.fieldVisit")}</dt>
          <dd>{row.visitId ?? t("lab.notRecorded")}</dd>
          <dt>{t("lab.fieldRequest")}</dt>
          <dd>{row.id}</dd>
          <dt>{t("lab.fieldTest")}</dt>
          <dd>{translateClinicalText(row.test[locale])}</dd>
          <dt>{t("lab.provider")}</dt>
          <dd>{row.provider}</dd>
          <dt>{t("lab.fieldRequestedBy")}</dt>
          <dd>{practitionerLabel(row.practitioner, locale)}</dd>
          <dt>{t("lab.fieldPriority")}</dt>
          <dd data-sample={row.priority ? "authored" : undefined}>
            {row.priority
              ? t(`lab.priority.${row.priority}`)
              : t("lab.notConfigured")}
          </dd>
          <dt>{t("lab.fieldSentAt")}</dt>
          <dd>
            {row.sentAt
              ? formatDateTime(row.sentAt, locale)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldRef")}</dt>
          <dd>{row.externalRef ?? t("lab.notRecorded")}</dd>
          <dt>{t("lab.fieldCategory")}</dt>
          <dd data-sample={row.category ? "authored" : undefined}>
            {row.category
              ? t(`lab.category.${row.category}`)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldSummary")}</dt>
          <dd data-sample={row.resultSummary ? "authored" : undefined}>
            {row.resultSummary
              ? translateClinicalText(row.resultSummary)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldFiles")}</dt>
          <dd>
            {fileNames.length === 0 ? (
              <span className="pc-note">{t("lab.noFiles")}</span>
            ) : (
              <ul className="pc-lab-files">
                {row.files.map((file) => (
                  <li key={file.name}>
                    <span>{file.name}</span>{" "}
                    <Badge tone="warning">{t("lab.privateFile")}</Badge>
                    <p className="pc-note">
                      {file.uploadedBy ?? t("lab.notRecorded")} ·{" "}
                      {file.uploadedAt
                        ? formatDateTime(file.uploadedAt, locale)
                        : t("lab.notRecorded")}{" "}
                      · {t("lab.openFileUnavailable")}
                    </p>
                  </li>
                ))}
                {row.files.length === 0 &&
                  fileNames.map((name) => (
                    <li key={name}>
                      <span>{name}</span>
                      <p className="pc-note">{t("lab.fileNameOnly")}</p>
                    </li>
                  ))}
              </ul>
            )}
          </dd>
          <dt>{t("lab.fieldUploadedBy")}</dt>
          <dd>
            {uploadedBy ?? t("lab.notRecorded")}
            {!uploadedBy && receivedBy && (
              <span className="pc-note">
                {" "}
                · {t("lab.receivedByProvenance", { name: receivedBy })}
              </span>
            )}
          </dd>
          <dt>{t("lab.receivedAt")}</dt>
          <dd>
            {row.receivedAt
              ? formatDateTime(row.receivedAt, locale)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldReviewedBy")}</dt>
          <dd>
            {row.reviewedBy
              ? `${practitionerLabel(row.reviewedBy, locale)} · ${row.reviewedBy}`
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldReviewedAt")}</dt>
          <dd>
            {row.reviewedAt
              ? formatDateTime(row.reviewedAt, locale)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldInterpretation")}</dt>
          <dd data-sample={row.interpretation ? "authored" : undefined}>
            {row.interpretation
              ? translateClinicalText(row.interpretation)
              : t("lab.notRecorded")}
          </dd>
          <dt>{t("lab.fieldStatus")}</dt>
          <dd>
            <LabStatusBadge status={row.status} /> ·{" "}
            <span className="pc-note">
              {t("lab.modelSupport")}: {t("lab.modelUnverified")}
            </span>
          </dd>
        </dl>

        {!linked && <p className="pc-note">{t("lab.requestNotLinked")}</p>}

        <div className="pc-row">
          {canSend && (
            <Button variant="secondary" onClick={() => onOpenDrawer("send")}>
              {t("lab.sendAction")}
            </Button>
          )}
          {canReceive && (
            <Button variant="primary" onClick={() => onOpenDrawer("receive")}>
              {t("lab.receiveAction")}
            </Button>
          )}
          {canReview && (
            <Button variant="primary" onClick={() => onOpenDrawer("review")}>
              {t("lab.reviewAction")}
            </Button>
          )}
          {isDoctor && row.status !== "received" && (
            <span className="pc-note">{t("lab.notReady")}</span>
          )}
          {isNurse && row.status === "received" && (
            <span className="pc-note">{t("lab.reviewRequiresDoctor")}</span>
          )}
          {isDoctor && row.visitId && (
            <Link
              className="pc-button pc-button--secondary"
              to={ROUTES.visit(row.visitId)}
            >
              {t("lab.openVisit")}
            </Link>
          )}
        </div>
      </div>
    </Card>
  );
}

function LabDrawer({
  mode,
  row,
  note,
  onClose,
  unavailableLabel,
}: {
  mode: LabDrawer;
  row: LabRequestView;
  note: string;
  onClose: () => void;
  unavailableLabel: string;
}) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const title =
    mode === "send"
      ? t("lab.sendTitle")
      : mode === "receive"
        ? t("lab.receiveTitle")
        : t("lab.reviewTitle");
  return (
    <Drawer
      title={title}
      onClose={onClose}
      footer={
        <div className="pc-row">
          <Button variant="ghost" onClick={onClose}>
            {t("action.close")}
          </Button>
          <Button variant="primary" disabled>
            {unavailableLabel}
          </Button>
        </div>
      }
    >
      <div className="pc-stack" data-testid={`lab-drawer-${mode}`}>
        <p className="pc-banner pc-banner--warn">
          <span className="pc-banner__body">{note}</span>
        </p>
        <p className="pc-note">{t("lab.sampleRecordNote")}</p>
        <dl className="pc-dl">
          <div>
            <dt>{t("lab.fieldRequest")}</dt>
            <dd>{row.id}</dd>
          </div>
          <div>
            <dt>{t("lab.provider")}</dt>
            <dd>{row.provider}</dd>
          </div>
          <div>
            <dt>{t("lab.sample")}</dt>
            <dd>{row.sampleKind}</dd>
          </div>
          <div>
            <dt>{t("lab.fieldRef")}</dt>
            <dd>{row.externalRef ?? t("lab.refPending")}</dd>
          </div>
        </dl>
        {mode === "receive" && (
          <div className="pc-field">
            <span className="pc-field__label">{t("lab.fieldCategory")}</span>
            <input
              aria-label={t("lab.fieldCategory")}
              value={
                row.category
                  ? t(`lab.category.${row.category}`)
                  : t("lab.notRecorded")
              }
              readOnly
              disabled
            />
          </div>
        )}
        {mode === "receive" && (
          <div className="pc-field">
            <span className="pc-field__label">{t("lab.fieldSummary")}</span>
            <textarea value={row.resultSummary ?? ""} readOnly disabled />
          </div>
        )}
        {mode === "receive" && (
          <div className="pc-field">
            <span className="pc-field__label">{t("lab.resultFile")}</span>
            <input aria-label={t("lab.resultFile")} type="file" disabled />
            <span className="pc-field__hint">{t("lab.resultFileHint")}</span>
          </div>
        )}
        {mode === "review" && (
          <div className="pc-field">
            <span className="pc-field__label">
              {t("lab.fieldInterpretation")}
            </span>
            <textarea
              aria-label={t("lab.fieldInterpretation")}
              value={row.interpretation ?? ""}
              placeholder={t("lab.interpretationPlaceholder")}
              readOnly
              disabled
            />
          </div>
        )}
        <p className="pc-note">{t("lab.receiveNotePolicy")}</p>
        <p className="pc-note">
          {t("lab.receivedAt")}:{" "}
          {row.receivedAt
            ? formatDateTime(row.receivedAt, locale)
            : t("lab.refPending")}{" "}
          · {t("lab.notReady")}
        </p>
      </div>
    </Drawer>
  );
}
