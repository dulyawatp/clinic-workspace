import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { Button } from "../../design/components";
import { fromWire } from "../../domain/errors";
import {
  loadNativeOwnerStaffRoleOptions,
  type OwnerStaffRoleOptions,
  type OwnerStaffRoleUpdate,
  updateNativeOwnerStaffRoles,
} from "../../transport/native/owner-staff";

type StaffIdentity = { userId: string; displayName: string };
type EditorFailure =
  | "denied"
  | "expired"
  | "validation"
  | "stale"
  | "uncertain"
  | "unavailable";

export function StaffRoleEditor({
  staff,
  onCancel,
  onReload,
  onSaved,
}: {
  staff: StaffIdentity;
  onCancel: () => void;
  onReload: () => void;
  onSaved: (update: OwnerStaffRoleUpdate) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const submitting = useRef(false);
  const [options, setOptions] = useState<OwnerStaffRoleOptions | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<EditorFailure | null>(null);

  useEffect(() => {
    heading.current?.focus();
    const controller = new AbortController();
    void loadNativeOwnerStaffRoleOptions(staff.userId, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setOptions(data);
        setSelected(data.roles);
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const code = fromWire(
          error && typeof error === "object" && "code" in error ? error.code : undefined,
        );
        if (code === "FORBIDDEN") setFailure("denied");
        else if (code === "AUTH_EXPIRED") setFailure("expired");
        else setFailure("unavailable");
      },
    );
    return () => controller.abort();
  }, [staff.userId]);

  const needsReload = failure === "stale" || failure === "uncertain";
  const canEdit = options && (!failure || failure === "validation");
  const changed = options && (
    selected.length !== options.roles.length ||
    selected.some((role) => !options.roles.includes(role))
  );

  async function save() {
    if (!options || !canEdit || !changed || submitting.current) return;
    submitting.current = true;
    setSaving(true);
    setFailure(null);
    const result = await updateNativeOwnerStaffRoles({
      userId: options.userId,
      version: options.version,
      roles: selected,
    });
    if (result.kind === "saved") {
      onSaved(result.update);
    } else {
      submitting.current = false;
      setSaving(false);
      setFailure(result.kind);
    }
  }

  return (
    <section
      className="owner-staff-edit"
      aria-labelledby={`${id}-title`}
      data-testid="owner-staff-role-editor"
    >
      <h3 ref={heading} tabIndex={-1} id={`${id}-title`}>
        {t("ownerStaff.editRoles")} · <bdi>{staff.displayName}</bdi>
      </h3>
      {staff.userId !== staff.displayName && (
        <bdi className="owner-staff-id">{staff.userId}</bdi>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {!options && !failure && <p role="status">{t("ownerStaff.rolesLoading")}</p>}
        {canEdit && (
          <>
            <p id={`${id}-hint`}>{t("ownerStaff.rolesHint")}</p>
            <fieldset
              className="owner-staff-choices"
              aria-describedby={`${id}-hint`}
              disabled={saving}
            >
              <legend>{t("ownerStaff.roleChoices")}</legend>
              <div className="owner-staff-choice-grid">
                {options.choices.map(({ role, label }) => (
                  <label className="owner-staff-choice" key={role}>
                    <input
                      type="checkbox"
                      name="roles"
                      value={role}
                      checked={selected.includes(role)}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setSelected((current) =>
                          checked ? [...current, role] : current.filter((item) => item !== role),
                        );
                        if (failure === "validation") setFailure(null);
                      }}
                    />
                    <bdi>{label}</bdi>
                  </label>
                ))}
              </div>
            </fieldset>
            {options.choices.length === 0 && <p>{t("ownerStaff.noRoleChoices")}</p>}
          </>
        )}
        {saving && (
          <p role="status" data-testid="owner-staff-role-saving">
            {t("ownerStaff.rolesSaving")}
          </p>
        )}
        {failure && (
          <p
            className="owner-staff-edit-alert"
            role="alert"
            data-testid={`owner-staff-role-${failure}`}
          >
            {t(`ownerStaff.roleFailure.${failure}`)}
          </p>
        )}
        <div className="owner-staff-page-buttons">
          {canEdit && (
            <Button
              variant="primary"
              type="submit"
              disabled={!changed || saving}
              data-testid="owner-staff-role-save"
            >
              {t("action.save")}
            </Button>
          )}
          {needsReload ? (
            <Button onClick={onReload} data-testid="owner-staff-role-reload">
              {t("ownerStaff.reloadDirectory")}
            </Button>
          ) : (
            <Button disabled={saving} onClick={onCancel} data-testid="owner-staff-role-cancel">
              {t("action.cancel")}
            </Button>
          )}
          {failure === "expired" && (
            <Link className="pc-button pc-button--secondary" to={`${ROUTES.login}?reason=expired`}>
              {t("page.signin.title")}
            </Link>
          )}
        </div>
      </form>
    </section>
  );
}

export function StaffRoleReceipt({
  staff,
  update,
}: {
  staff: StaffIdentity;
  update: OwnerStaffRoleUpdate;
}) {
  const { t } = useTranslation();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <section className="owner-staff-edit owner-staff-receipt" data-testid="owner-staff-role-result">
      <h3 tabIndex={-1} ref={heading}>
        {t("ownerStaff.rolesSaved")} · <bdi>{staff.displayName}</bdi>
      </h3>
      {staff.userId !== staff.displayName && (
        <bdi className="owner-staff-id">{staff.userId}</bdi>
      )}
      <dl className="owner-staff-details">
        <div className="owner-staff-result-roles">
          <dt>{t("ownerStaff.resultingRoles")}</dt>
          <dd data-testid="owner-staff-resulting-roles">
            <bdi>{update.afterRoles.join(", ") || t("ownerStaff.none")}</bdi>
          </dd>
        </div>
        <div>
          <dt>{t("ownerStaff.automaticAdded")}</dt>
          <dd data-testid="owner-staff-automatic-added">
            <bdi>{update.automaticAdded.join(", ") || t("ownerStaff.none")}</bdi>
          </dd>
        </div>
        <div>
          <dt>{t("ownerStaff.automaticRemoved")}</dt>
          <dd data-testid="owner-staff-automatic-removed">
            <bdi>{update.automaticRemoved.join(", ") || t("ownerStaff.none")}</bdi>
          </dd>
        </div>
        {update.beforeUserType !== update.afterUserType && (
          <div className="owner-staff-result-roles">
            <dt>{t("ownerStaff.automaticUserType")}</dt>
            <dd data-testid="owner-staff-user-type-change">
              <bdi>{update.beforeUserType}</bdi> → <bdi>{update.afterUserType}</bdi>
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}
