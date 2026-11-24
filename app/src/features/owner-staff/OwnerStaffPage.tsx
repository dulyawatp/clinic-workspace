import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { Badge, Button, Card, PageHeader } from "../../design/components";
import { fromWire } from "../../domain/errors";
import {
  listNativeOwnerStaff,
  OWNER_STAFF_PAGE_SIZE,
  type OwnerStaffPageData,
  type OwnerStaffRoleUpdate,
} from "../../transport/native/owner-staff";
import { StatePanel } from "../../ui/StatePanel";
import { canViewOwnerStaff } from "./access";
import { AddDoctor } from "./AddDoctor";
import { AddStaff } from "./AddStaffForm";
import { DoctorInvitation } from "./DoctorInvitation";
import { StaffRoleEditor, StaffRoleReceipt } from "./StaffRoleEditor";
import "./owner-staff.css";

const reasonKeys: ReadonlyMap<string, string> = new Map([
  ["self", "ownerStaff.reason.self"],
  ["protected_identity", "ownerStaff.reason.protectedIdentity"],
  ["privileged_account", "ownerStaff.reason.privilegedAccount"],
  ["role_profile", "ownerStaff.reason.roleProfile"],
  ["user_type", "ownerStaff.reason.userType"],
  ["staff_controls_unavailable", "ownerStaff.readOnly"],
]);

type StaffIdentity = { userId: string; displayName: string };

function StaffDenied() {
  const { t } = useTranslation();
  return (
    <div data-testid="owner-staff-denied">
      <StatePanel
        kind="permission-denied"
        title={t("state.denied.title")}
        detail={t("ownerStaff.denied")}
      />
      <p>
        <Link className="pc-button pc-button--secondary" to={ROUTES.home}>
          {t("action.goMyHome")}
        </Link>
      </p>
    </div>
  );
}

function StaffDirectory() {
  const { t } = useTranslation();
  const pageLabel = useRef<HTMLParagraphElement>(null);
  const editTrigger = useRef<HTMLButtonElement | null>(null);
  const invitePanelId = useId();
  const [invitationUser, setInvitationUser] = useState<string | null>(null);
  const [sendingInvitation, setSendingInvitation] = useState(false);
  const [editing, setEditing] = useState<StaffIdentity | null>(null);
  const [receipt, setReceipt] = useState<{
    staff: StaffIdentity;
    update: OwnerStaffRoleUpdate;
  } | null>(null);
  const [request, setRequest] = useState({ start: 0 });
  const [result, setResult] = useState<
    | { kind: "loading" }
    | { kind: "ready"; page: OwnerStaffPageData }
    | { kind: "denied" | "expired" | "error" }
  >({ kind: "loading" });
  const { start } = request;

  useEffect(() => {
    const controller = new AbortController();
    void listNativeOwnerStaff(request.start, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) setResult({ kind: "ready", page });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const code = fromWire(
          error && typeof error === "object" && "code" in error
            ? error.code
            : undefined,
        );
        if (code === "FORBIDDEN") setResult({ kind: "denied" });
        else if (code === "AUTH_EXPIRED") setResult({ kind: "expired" });
        else setResult({ kind: "error" });
      },
    );
    return () => controller.abort();
  }, [request]);

  function loadAt(nextStart: number) {
    setInvitationUser(null);
    setSendingInvitation(false);
    setResult({ kind: "loading" });
    setRequest({ start: nextStart });
  }

  if (result.kind === "denied") return <StaffDenied />;
  if (result.kind === "expired") {
    return (
      <div data-testid="owner-staff-expired">
        <StatePanel kind="error" title={t("errors.AUTH_EXPIRED")} />
        <p>
          <Link
            className="pc-button pc-button--secondary"
            to={`${ROUTES.login}?reason=expired`}
          >
            {t("page.signin.title")}
          </Link>
        </p>
      </div>
    );
  }

  const page = result.kind === "ready" ? result.page : null;
  const size = page?.pageSize ?? OWNER_STAFF_PAGE_SIZE;
  const previous = !editing && !sendingInvitation && start > 0 && result.kind !== "loading";
  const next = !editing && !sendingInvitation && page?.hasMore === true;

  return (
    <Card title={t("ownerStaff.directory")} flush>
      <AddStaff onReload={() => loadAt(start)} />
      <nav
        className="owner-staff-pager"
        aria-label={t("ownerStaff.pagination")}
      >
        <p
          ref={pageLabel}
          tabIndex={-1}
          className="owner-staff-page-label"
          role="status"
          aria-atomic="true"
          data-testid="owner-staff-page-status"
        >
          <strong>{t("ownerStaff.page", { page: start / size + 1 })}</strong>
          {page && page.staff.length > 0 && (
            <span>
              {t("ownerStaff.range", {
                first: page.start + 1,
                last: page.start + page.staff.length,
              })}
            </span>
          )}
        </p>
        <div className="owner-staff-page-buttons">
          <Button
            aria-disabled={!previous}
            onClick={() => {
              if (previous) loadAt(start - size);
            }}
            data-testid="owner-staff-previous"
          >
            {t("ownerStaff.previous")}
          </Button>
          <Button
            aria-disabled={!next}
            onClick={() => {
              if (next) loadAt(start + size);
            }}
            data-testid="owner-staff-next"
          >
            {t("ownerStaff.next")}
          </Button>
        </div>
      </nav>

      {editing && (
        <StaffRoleEditor
          staff={editing}
          onCancel={() => {
            setEditing(null);
            editTrigger.current?.focus();
          }}
          onReload={() => {
            setEditing(null);
            pageLabel.current?.focus();
            loadAt(start);
          }}
          onSaved={(update) => {
            setReceipt({ staff: editing, update });
            setEditing(null);
            loadAt(start);
          }}
        />
      )}
      {receipt && <StaffRoleReceipt staff={receipt.staff} update={receipt.update} />}

      {page && page.staff.length > 0 ? (
        <ul className="owner-staff-list" data-testid="owner-staff-list">
          {page.staff.map((staff) => {
            const accountStatus = staff.enabled
              ? "ownerStaff.enabled"
              : "ownerStaff.disabled";
            const roles = staff.roles
              .map((role) => t(`role.${role.toLowerCase()}.short`))
              .join(", ");
            const editable = staff.editable && staff.protectedReason === null;
            // Directory roles are mapped codes, not proof of a linked practitioner.
            const invitationCandidate = staff.enabled && staff.roles.length === 1 && staff.roles[0] === "DOC";
            const invitationOpen = invitationUser === staff.userId;
            const reasonKey = staff.protectedReason
              ? (reasonKeys.get(staff.protectedReason) ??
                "ownerStaff.restricted")
              : editable
                ? "ownerStaff.noRestrictionReported"
                : "ownerStaff.readOnly";
            return (
              <li
                key={staff.userId}
                className="owner-staff-row"
                data-testid="owner-staff-row"
              >
                <div className="owner-staff-identity">
                  <h3>
                    <bdi>{staff.displayName}</bdi>
                  </h3>
                  {staff.userId !== staff.displayName && (
                    <bdi className="owner-staff-id">{staff.userId}</bdi>
                  )}
                </div>
                <dl className="owner-staff-details">
                  <div>
                    <dt>{t("ownerStaff.accountStatus")}</dt>
                    <dd>
                      <Badge tone={staff.enabled ? "success" : "neutral"}>
                        {t(accountStatus)}
                      </Badge>
                    </dd>
                  </div>
                  <div>
                    <dt>{t("ownerStaff.clinicRoles")}</dt>
                    <dd>{roles || t("ownerStaff.noClinicRoles")}</dd>
                  </div>
                  <div className="owner-staff-restriction">
                    <dt>{t("ownerStaff.restrictions")}</dt>
                    <dd>
                      {t(reasonKey)}
                      {editable && (
                        <div className="owner-staff-edit-trigger">
                          <Button
                            aria-label={t("ownerStaff.editRolesFor", { name: staff.displayName })}
                            aria-disabled={editing !== null}
                            data-testid="owner-staff-edit-roles"
                            onClick={(event) => {
                              if (editing) return;
                              editTrigger.current = event.currentTarget;
                              setReceipt(null);
                              setEditing(staff);
                            }}
                          >
                            {t("ownerStaff.editRoles")}
                          </Button>
                        </div>
                      )}
                    </dd>
                  </div>
                </dl>
                {invitationCandidate && (
                  <div className="owner-staff-invitation">
                    <Button
                      aria-expanded={invitationOpen}
                      aria-controls={invitationOpen ? invitePanelId : undefined}
                      aria-disabled={sendingInvitation}
                      aria-label={t(invitationOpen ? "ownerDoctor.invitation.closeFor" : "ownerDoctor.invitation.openFor", { name: staff.displayName })}
                      data-testid="owner-staff-open-doctor-invitation"
                      onClick={() => {
                        if (!sendingInvitation) setInvitationUser(invitationOpen ? null : staff.userId);
                      }}
                    >
                      {t(invitationOpen ? "ownerDoctor.invitation.close" : "ownerDoctor.invitation.open")}
                    </Button>
                    {invitationOpen && (
                      <div id={invitePanelId} data-testid="owner-staff-doctor-invitation-panel">
                        <DoctorInvitation userId={staff.userId} onPendingChange={setSendingInvitation} />
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <div
          className="owner-staff-state"
          data-testid={`owner-staff-${result.kind === "ready" ? "empty" : result.kind}`}
        >
          {result.kind === "loading" && (
            <StatePanel kind="loading" title={t("ownerStaff.loading")} />
          )}
          {result.kind === "ready" && (
            <div role="status">
              <StatePanel
                kind="empty"
                title={t(start ? "ownerStaff.emptyPage" : "ownerStaff.empty")}
              />
            </div>
          )}
          {result.kind === "error" && (
            <StatePanel
              kind="error"
              title={t("ownerStaff.error")}
              retryLabel={t("action.retry")}
              onRetry={() => {
                pageLabel.current?.focus();
                loadAt(start);
              }}
            />
          )}
        </div>
      )}
    </Card>
  );
}

export function OwnerStaffRoute() {
  const { t } = useTranslation();
  const { session } = useSession();
  const [directoryRevision, setDirectoryRevision] = useState(0);
  const allowed =
    session &&
    canViewOwnerStaff(session.mode, session.user.roles, session.capabilities);

  return (
    <div data-testid="owner-staff">
      <PageHeader
        title={t("nav.staff")}
        subtitle={allowed ? t("ownerStaff.description") : undefined}
      />
      <div className="pc-page__body">
        {allowed ? (
          <>
            <AddDoctor
              key={session.sessionEpoch}
              onFinished={() => setDirectoryRevision((revision) => revision + 1)}
            />
            <StaffDirectory key={`${session.sessionEpoch}:${directoryRevision}`} />
          </>
        ) : <StaffDenied />}
      </div>
    </div>
  );
}
