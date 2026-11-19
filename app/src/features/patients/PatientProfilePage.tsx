import access from "@clinic/contracts/fixtures/access.json" with {
  type: "json",
};
import { type KeyboardEvent, type ReactNode, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "react-router-dom";
import { RequireSession } from "../../app/guards";
import { useSession } from "../../app/SessionProvider";
import type { AppointmentSummary } from "../../domain/appointment";
import {
  getCapability,
  isRoleCode,
  type RoleCode,
} from "../../domain/capability";
import { toI18nKey } from "../../domain/errors";
import {
  type BillingPatientSummary,
  type LatestVitals,
  mapPaymentStatus,
  type PatientSummary,
  type PaymentEntry,
  type TimelineEntry,
  type TimelineKind,
} from "../../domain/patient";
import { mapQueueStatus } from "../../domain/queue";
import { coreData } from "../../fixtures/data";
import { formatDate, formatDateTime, formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { nativeTransportEnabled } from "../../transport/native/flag";
import type { ReadParams } from "../../transport/read/client";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { StatusChip } from "../../ui/StatusChip";
import { useCapabilities } from "../shell/CapabilityProvider";
import { ConsentEvidence } from "./ConsentEvidence";
import { useLivePatient } from "./live-patient";
import { PatientHeader } from "./PatientHeader";
import { RedactionNotice } from "./RedactionNotice";
import { SummaryCard } from "./SummaryCard";
import { PatientWriteForm } from "./PatientWriteForm";
import { Timeline } from "./Timeline";
import { VisitTimeline } from "./VisitTimeline";
import { VitalsSummary } from "./VitalsSummary";

const PROFILE_LIMIT = 20;

/**
 * The same interim matrix the proxy and MSW enforce decides which record the
 * profile may read: a role the front-desk patient resource denies falls back to
 * the identity-only billing record instead of a full clinical header. Still
 * presentation only, never authorization.
 */
const FRONTDESK_PATIENT_ROLES: readonly RoleCode[] =
  access.resources["frontdesk-patients"].filter(isRoleCode);

type TabId =
  | "appointments"
  | "visits"
  | "labs"
  | "treatments"
  | "photos"
  | "payments";

interface TabBase {
  id: TabId;
  labelKey: string;
  /**
   * Presentation only. The proxy read matrix remains the interim gate, and a
   * 403 renders inside the tab instead of hiding the denial.
   */
  roles: readonly RoleCode[];
}

/**
 * A history tab always names its timeline kind; the appointment and payment
 * tabs own their own resources. The union removes the "tab without a kind"
 * case instead of leaving a fallback that can never run.
 */
type TabDefinition =
  | (TabBase & { id: "appointments" })
  | (TabBase & { id: "payments" })
  | (TabBase & {
      id: "visits" | "labs" | "treatments" | "photos";
      kind: TimelineKind;
    });

const TABS: readonly TabDefinition[] = [
  {
    id: "appointments",
    labelKey: "profile.tab.appointments",
    roles: ["REC", "DOC", "NUR", "ADM"],
  },
  {
    id: "visits",
    labelKey: "profile.tab.visits",
    roles: ["DOC", "NUR"],
    kind: "visit",
  },
  {
    id: "labs",
    labelKey: "profile.tab.labs",
    roles: ["DOC", "NUR"],
    kind: "lab",
  },
  {
    id: "treatments",
    labelKey: "profile.tab.treatments",
    roles: ["DOC", "NUR"],
    kind: "treatment",
  },
  {
    id: "photos",
    labelKey: "profile.tab.photos",
    roles: ["REC", "DOC", "NUR"],
    kind: "photo",
  },
  {
    // Payment history is a billing surface: never rendered for a nurse or a
    // doctor, and never derived from a visit row they can see.
    id: "payments",
    labelKey: "profile.tab.payments",
    roles: ["CAS", "MGR"],
  },
];

/**
 * One read per tab, with the denial rendered in place. A 403 is a permission
 * state, not an empty list: it never falls back to "no records".
 */
function TabBody<T>({
  resource,
  params,
  emptyKey,
  children,
}: {
  resource: string;
  params: ReadParams;
  emptyKey: string;
  children: (items: readonly T[]) => ReactNode;
}) {
  const { t } = useTranslation();
  const query = useReadQuery<T>(resource, params);

  if (query.isError) {
    if (query.error.code === "FORBIDDEN")
      return (
        <StatePanel
          kind="permission-denied"
          title={t("state.denied.title")}
          detail={t("state.denied.body", { resource: t("surface.PAT-03") })}
        />
      );
    return (
      <StatePanel
        kind="error"
        title={t(toI18nKey(query.error))}
        onRetry={() => {
          void query.refetch();
        }}
        retryLabel={t("action.retry")}
      />
    );
  }
  // Cached success is not confirmation for this observer.
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (query.data.items.length === 0)
    return <StatePanel kind="empty" title={t(emptyKey)} />;
  return <>{children(query.data.items)}</>;
}

function AppointmentsPanel({ patientId }: { patientId: string }) {
  const { locale } = useLocale();
  const { t } = useTranslation();
  return (
    <TabBody<AppointmentSummary>
      resource="frontdesk-appointments"
      params={{ filters: { patientId }, limit: PROFILE_LIMIT }}
      emptyKey="profile.appointmentsEmpty"
    >
      {(items) => (
        <ol className="patients-timeline">
          {items.map((appointment) => (
            <li key={appointment.id} className="patients-timeline-item">
              <time
                className="patients-timeline-time"
                dateTime={appointment.startsAt}
              >
                {formatDateTime(appointment.startsAt, locale)}
              </time>
              <span className="patients-timeline-kind">
                {appointment.practitionerName}
              </span>
              <StatusChip
                tone="neutral"
                label={(() => {
                  const status = mapQueueStatus(appointment.status);
                  return status.labelKey
                    ? t(status.labelKey)
                    : `${status.raw} · ${t("status.unmapped")}`;
                })()}
              />
              <span className="patients-id">{appointment.id}</span>
            </li>
          ))}
        </ol>
      )}
    </TabBody>
  );
}

function TimelinePanel({
  patientId,
  kind,
}: {
  patientId: string;
  kind: TimelineKind;
}) {
  return (
    <TabBody<TimelineEntry>
      resource="frontdesk-timeline"
      params={{ filters: { patientId, kind }, limit: PROFILE_LIMIT }}
      emptyKey="profile.historyEmpty"
    >
      {(items) => (
        <Timeline items={items.filter((item) => item.kind === kind)} />
      )}
    </TabBody>
  );
}

function PaymentsPanel({ patientId }: { patientId: string }) {
  const { locale } = useLocale();
  const { t } = useTranslation();
  return (
    <TabBody<PaymentEntry>
      resource="frontdesk-payments"
      params={{ filters: { patientId }, limit: PROFILE_LIMIT }}
      emptyKey="profile.paymentsEmpty"
    >
      {(items) => (
        <ol className="patients-timeline">
          {items.map((payment) => (
            <li key={payment.id} className="patients-timeline-item">
              <time
                className="patients-timeline-time"
                dateTime={payment.issuedAt}
              >
                {formatDateTime(payment.issuedAt, locale)}
              </time>
              <span className="patients-id">{payment.id}</span>
              <StatusChip
                tone="neutral"
                label={
                  mapPaymentStatus(payment.status)
                    ? t(mapPaymentStatus(payment.status) ?? "")
                    : `${payment.status} · ${t("status.unmapped")}`
                }
              />
              <span>{formatMoney(payment.total, locale)}</span>
            </li>
          ))}
        </ol>
      )}
    </TabBody>
  );
}

/**
 * Latest vitals are chosen by measurement time, not by fixture order. An
 * upstream failure is an error, never "no measurements yet".
 */
function VitalsPanel({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const query = useReadQuery<LatestVitals>("frontdesk-vitals", {
    filters: { patientId },
    limit: PROFILE_LIMIT,
  });

  if (query.isError)
    return (
      <StatePanel
        kind="error"
        title={t(toI18nKey(query.error))}
        onRetry={() => {
          void query.refetch();
        }}
        retryLabel={t("action.retry")}
      />
    );
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;

  const latest = query.data.items.reduce<LatestVitals | undefined>(
    (newest, row) =>
      newest === undefined || row.measuredAt > newest.measuredAt ? row : newest,
    undefined,
  );
  return <VitalsSummary latest={latest} />;
}

function ProfileTab({
  tab,
  patientId,
}: {
  tab: TabDefinition;
  patientId: string;
}) {
  switch (tab.id) {
    case "appointments":
      return <AppointmentsPanel patientId={patientId} />;
    case "payments":
      return <PaymentsPanel patientId={patientId} />;
    default:
      return <TimelinePanel patientId={patientId} kind={tab.kind} />;
  }
}

function ProfileBody({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { caps } = useCapabilities();
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  // Any role the front-desk patient resource denies reads the identity-only
  // billing record. A session without roles keeps the front-desk read and
  // renders the denial rather than picking a record type.
  const billingOnly =
    roles.length > 0 &&
    !FRONTDESK_PATIENT_ROLES.some((role) => roles.includes(role));
  const [selectedTab, setSelectedTab] = useState<TabId>("appointments");

  // The route is the only source of the addressed record: no "first row"
  // fallback may stand in for a missing id.
  const profileQuery = useReadQuery<PatientSummary | BillingPatientSummary>(
    billingOnly ? "billing-patient-summary" : "frontdesk-patients",
    {
      filters: { id: patientId },
      limit: 1,
    },
  );

  const capability = getCapability(
    caps,
    billingOnly ? "read.billing" : "read.patient",
  );
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <div className="shell-page-body">
        <StatePanel
          kind="permission-denied"
          title={t("state.denied.title")}
          detail={t("state.denied.body", { resource: t("surface.PAT-03") })}
        />
      </div>
    );
  if (capability === "error_upstream")
    return (
      <div className="shell-page-body">
        <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
      </div>
    );

  const patient = profileQuery.data?.items[0];

  return (
    <>
      <PageHeader title={t("surface.PAT-03")} />
      <div className="shell-page-body">
        {profileQuery.isError ? (
          <StatePanel
            kind="error"
            title={t(toI18nKey(profileQuery.error))}
            onRetry={() => {
              void profileQuery.refetch();
            }}
            retryLabel={t("action.retry")}
          />
        ) : profileQuery.isPending ||
          profileQuery.isFetching ||
          !profileQuery.isFetchedAfterMount ? (
          <StatePanel kind="loading" title={t("common.loading")} />
        ) : patient === undefined ? (
          <StatePanel kind="empty" title={t("profile.notFound")} />
        ) : (
          <ProfileView
            patient={patient}
            roles={roles}
            billingOnly={billingOnly}
            selectedTab={selectedTab}
            onSelectTab={setSelectedTab}
          />
        )}
      </div>
    </>
  );
}

function ProfileView({
  patient,
  roles,
  billingOnly,
  selectedTab,
  onSelectTab,
}: {
  patient: PatientSummary | BillingPatientSummary;
  roles: readonly RoleCode[];
  billingOnly: boolean;
  selectedTab: TabId;
  onSelectTab: (tab: TabId) => void;
}) {
  const { t } = useTranslation();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const visible = TABS.filter((tab) =>
    tab.roles.some((role) => roles.includes(role)),
  );
  const active =
    visible.find((tab) => tab.id === selectedTab) ?? visible[0] ?? undefined;

  /** ARIA APG tabs pattern: arrows move focus and selection, Home/End jump. */
  function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
    if (active === undefined) return;
    const index = visible.findIndex((tab) => tab.id === active.id);
    if (index < 0) return;

    let next: number;
    switch (event.key) {
      case "ArrowRight":
        next = (index + 1) % visible.length;
        break;
      case "ArrowLeft":
        next = (index - 1 + visible.length) % visible.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = visible.length - 1;
        break;
      default:
        return;
    }

    event.preventDefault();
    const target = visible[next];
    if (target === undefined) return;
    onSelectTab(target.id);
    tabRefs.current[next]?.focus();
  }

  const canOpenPhotos = roles.some(
    (role) => role === "REC" || role === "DOC" || role === "NUR",
  );

  return (
    <>
      <PatientHeader patient={patient} billingOnly={billingOnly} />
      {!billingOnly && (
        <div className="patients-summary-grid">
          <SummaryCard patient={patient} canOpenPhotos={canOpenPhotos} />
          <VitalsPanel patientId={patient.id} />
          <ConsentEvidence
            patientId={patient.id}
            canOpenPhotos={canOpenPhotos}
          />
        </div>
      )}
      {!billingOnly && <RedactionNotice roles={roles} />}
      <section className="patients-card" aria-label={t("profile.history")}>
        <div
          className="patients-tabs"
          role="tablist"
          aria-label={t("profile.history")}
          onKeyDown={moveFocus}
        >
          {visible.map((tab, index) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`patient-tab-${tab.id}`}
              className="patients-tab"
              aria-selected={active?.id === tab.id}
              aria-controls={`patient-tabpanel-${tab.id}`}
              tabIndex={active?.id === tab.id ? 0 : -1}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              onClick={() => onSelectTab(tab.id)}
            >
              {t(tab.labelKey)}
            </button>
          ))}
        </div>
        {active === undefined ? (
          <StatePanel
            kind="permission-denied"
            title={t("state.denied.title")}
          />
        ) : (
          <div
            role="tabpanel"
            id={`patient-tabpanel-${active.id}`}
            aria-labelledby={`patient-tab-${active.id}`}
            className="patients-tabpanel"
          >
            <ProfileTab tab={active} patientId={patient.id} />
          </div>
        )}
      </section>
    </>
  );
}

/**
 * Native detail read for one allowlisted patient. It renders the same header
 * blocks and facts list as the existing profile, but only the fields the native
 * view returns: no history tabs, vitals or actions are requested, because those
 * reads are not part of this slice and their proxy transport must not be used
 * as a fallback.
 */
function NativePatientProfile({
  workspacePatientId,
}: {
  workspacePatientId: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t("surface.PAT-03")} />
      <NativePatientProfileContent workspacePatientId={workspacePatientId} />
    </>
  );
}

function NativePatientProfileContent({
  workspacePatientId,
}: {
  workspacePatientId: string;
}) {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const { locale } = useLocale();
  const { refresh, session } = useSession();
  const { caps } = useCapabilities();
  const capability = getCapability(caps, "read.patient");
  const query = useLivePatient(workspacePatientId);
  const unavailable = t("common.unavailable");

  // Presentation only: the native method re-authorizes every request. A role
  // outside the patient surfaces renders the denial instead of an endless
  // loading state, and no request is made.
  if (
    !session?.user.roles.some(
      (role) => role === "DOC" || role === "REC" || role === "NUR",
    ) ||
    capability === "denied" ||
    capability === "blocked_unverified"
  )
    return (
      <div className="shell-page-body">
        <StatePanel
          kind="permission-denied"
          title={t("state.denied.title")}
          detail={t("state.denied.body", { resource: t("surface.PAT-03") })}
        />
      </div>
    );
  if (capability === "error_upstream")
    return (
      <div className="shell-page-body">
        <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />
      </div>
    );

  if (query.isError) {
    if (query.error.code === "FORBIDDEN")
      return (
        <div className="shell-page-body">
          <StatePanel
            kind="permission-denied"
            title={t("state.denied.title")}
            detail={t("state.denied.body", { resource: t("surface.PAT-03") })}
          />
        </div>
      );
    if (query.error.code === "AUTH_EXPIRED")
      return (
        <div className="shell-page-body">
          <StatePanel
            kind="error"
            title={t(toI18nKey(query.error))}
            onRetry={() => {
              void refresh();
            }}
            retryLabel={t("action.retry")}
          />
        </div>
      );
    return (
      <div className="shell-page-body">
        {query.error.retryable ? (
          <StatePanel
            kind="error"
            title={t(toI18nKey(query.error))}
            onRetry={() => {
              void query.refetch();
            }}
            retryLabel={t("action.retry")}
          />
        ) : (
          <StatePanel kind="error" title={t(toI18nKey(query.error))} />
        )}
      </div>
    );
  }
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return (
      <div className="shell-page-body">
        <StatePanel kind="loading" title={t("common.loading")} />
      </div>
    );

  const detail = query.data;
  const sexLabels = new Map([
    ["male", t("patient.sexMale")],
    ["female", t("patient.sexFemale")],
    ["other", t("patient.sexOther")],
    ["unknown", t("patient.sexUnknown")],
  ]);
  const statusLabels = new Map([
    ["active", t("status.active")],
    ["inactive", t("status.inactive")],
  ]);
  return (
    <>
      <div className="shell-page-body">
        <header className="patients-profile-header">
          <div className="patients-profile-identity">
            <h2 className="patients-profile-name">{detail.display_name}</h2>
            <p className="patients-profile-id">
              <span>{t("patients.workspaceReference")}</span>{" "}
              <span className="patients-id" data-testid="patient-id">
                {detail.workspace_patient_id}
              </span>
            </p>
            <dl className="patients-profile-facts">
              <div>
                <dt>{t("profile.dob")}</dt>
                <dd>
                  {detail.dob ? formatDate(detail.dob, locale) : unavailable}
                </dd>
              </div>
              <div>
                <dt>{t("field.sex")}</dt>
                <dd>
                  {detail.sex === null
                    ? unavailable
                    : sexLabels.get(detail.sex.trim().toLowerCase()) ?? detail.sex}
                </dd>
              </div>
              <div>
                <dt>{t("rec.status")}</dt>
                <dd>
                  {detail.status === null
                    ? unavailable
                    : statusLabels.get(detail.status.trim().toLowerCase()) ?? detail.status}
                </dd>
              </div>
            </dl>
          </div>
        </header>
        <RedactionNotice roles={session?.user.roles ?? []} />
        {params.get("view") === "history" && (
          <p role="status">{t("workspace.nativeHistoryUnavailable")}</p>
        )}
        {getCapability(caps, "write.patient.update") === "enabled" && detail.editable === true && <PatientWriteForm patient={detail} />}
        {getCapability(caps, "write.patient.update") === "enabled" && detail.editable === false && <p role="status">{t("patientWrite.notEditable")}</p>}
      </div>
    </>
  );
}

/** Resolves the addressed record. No `:id` means no profile to show. */
function AddressedProfile() {
  const { t } = useTranslation();
  const params = useParams();
  const [search] = useSearchParams();
  const { session } = useSession();
  const { caps } = useCapabilities();
  const patientId = params.id;

  if (patientId === undefined)
    return (
      <>
        <PageHeader title={t("surface.PAT-03")} />
        <div className="shell-page-body">
          <StatePanel kind="empty" title={t("profile.notFound")} />
        </div>
      </>
    );

  // A live native session reads the record through the native method only. The
  // fixture and legacy proxy profiles stay exactly as they were.
  if (nativeTransportEnabled() && session?.mode === "live")
    return <NativePatientProfile workspacePatientId={patientId} />;

  // Explicit authored history; never use sample data as a fallback for a
  // failed patient read. Reuse the existing Visits tab audience.
  const patientRead = getCapability(caps, "read.patient");
  if (search.get("view") === "history" && session && session.mode !== "live" &&
      TABS.some((tab) => tab.id === "visits" && tab.roles.some((role) => session.user.roles.includes(role))) &&
      (patientRead === "enabled" || patientRead === "read_only") &&
      coreData.visitsFor(patientId).length > 0) {
    return (
      <>
        <PageHeader title={t("workspace.history")} />
        <div className="shell-page-body">
          <VisitTimeline patientId={patientId} encounterId={search.get("visit")} />
        </div>
      </>
    );
  }

  return <ProfileBody patientId={patientId} />;
}

export function PatientProfilePage() {
  return (
    <RequireSession>
      <AddressedProfile />
    </RequireSession>
  );
}
