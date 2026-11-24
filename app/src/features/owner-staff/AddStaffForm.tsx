import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button } from "../../design/components";
import { captureClinicRequestContext } from "../../transport/native/frappe";
import {
  createNativeOwnerStaff,
  loadNativeOwnerStaffCreateChoices,
  type OwnerStaffCreateChoices,
  type OwnerStaffCreateInput,
  type OwnerStaffCreateResult,
} from "../../transport/native/owner-staff-create";
import "./owner-staff.css";

type AddStaffState = OwnerStaffCreateResult | { kind: "idle" | "pending" | "stale" };

type AddStaffFormProps = {
  choices: OwnerStaffCreateChoices;
  state: AddStaffState;
  onSubmit: (input: OwnerStaffCreateInput) => void;
  onReload: () => void;
};

const identityFields = [
  { name: "firstName", autoComplete: "given-name", type: "text" },
  { name: "lastName", autoComplete: "family-name", type: "text" },
  { name: "email", autoComplete: "email", type: "email" },
] as const;
type IdentityField = (typeof identityFields)[number]["name"];
type FieldName = IdentityField | "roles";
type FieldErrors = Partial<Record<FieldName, string>>;
const fieldOrder: FieldName[] = [...identityFields.map(({ name }) => name), "roles"];

export function AddStaff({ onReload }: { onReload: () => void }) {
  const [options, setOptions] = useState<{
    choices: OwnerStaffCreateChoices;
    scope: AbortSignal;
    isCurrent: () => boolean;
  } | null>(null);
  const [state, setState] = useState<AddStaffState>({ kind: "idle" });
  const writing = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const scope = controller.signal;
    const isCurrent = captureClinicRequestContext();
    void loadNativeOwnerStaffCreateChoices(scope).then(
      (choices) => {
        // The native creation endpoint supplies only approved ordinary staff roles.
        if (!scope.aborted && isCurrent() && choices.length) {
          setOptions({ choices, scope, isCurrent });
        }
      },
      () => {
        // Without server-approved choices there is no creation form.
      },
    );
    return () => controller.abort();
  }, []);

  if (!options) return null;
  const { choices, scope, isCurrent } = options;
  return (
    <AddStaffForm
      choices={choices}
      state={state}
      onReload={onReload}
      onSubmit={async (input) => {
        if (writing.current || scope.aborted) return;
        if (!isCurrent()) {
          setState({ kind: "stale" });
          return;
        }
        writing.current = true;
        setState({ kind: "pending" });
        const result = await createNativeOwnerStaff(input);
        writing.current = false;
        if (scope.aborted) return;
        if (!isCurrent()) {
          setState({ kind: "uncertain" });
          return;
        }
        setState(result);
        if (result.kind === "created") onReload();
      }}
    />
  );
}

function AddStaffForm({ choices, state, onSubmit, onReload }: AddStaffFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const form = useRef<HTMLFormElement>(null);
  const focusNext = useRef<FieldName | undefined>(undefined);
  const [identity, setIdentity] = useState({ firstName: "", lastName: "", email: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submittedEmail, setSubmittedEmail] = useState("");
  const pending = state.kind === "pending";
  const failure = state.kind === "denied" || state.kind === "validation" ||
    state.kind === "uncertain" || state.kind === "expired" || state.kind === "stale"
    ? state.kind
    : undefined;
  const locked = pending || state.kind === "created" || Boolean(failure && failure !== "validation");
  const duplicate = state.kind === "duplicate" &&
    identity.email.trim().toLowerCase() === submittedEmail.toLowerCase();
  const receipt = state.kind === "created" ? state.account : null;
  const rolesInvalid = Boolean(errors.roles);
  const rolesHelp = rolesInvalid ? "rolesRequired" : "rolesHint";

  useEffect(() => {
    if (!focusNext.current) return;
    form.current?.querySelector<HTMLInputElement>(`input[name="${focusNext.current}"]`)?.focus();
    focusNext.current = undefined;
  }, [errors]);

  function clearError(name: FieldName) {
    setErrors((current) => current[name] ? { ...current, [name]: undefined } : current);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || duplicate) return;

    const firstName = identity.firstName.trim();
    const lastName = identity.lastName.trim();
    const email = identity.email.trim();
    const emailInput = event.currentTarget.elements.namedItem("email") as HTMLInputElement;
    const selected = new FormData(event.currentTarget).getAll("roles");
    const roles = choices
      .filter(({ role }) => selected.includes(role))
      .map(({ role }) => role);
    const next: FieldErrors = {};
    if (!firstName) next.firstName = "firstNameRequired";
    if (!lastName) next.lastName = "lastNameRequired";
    if (!email) next.email = "emailRequired";
    else if (emailInput.validity.typeMismatch) next.email = "emailInvalid";
    if (!roles.length) next.roles = "rolesRequired";
    const firstInvalid = fieldOrder.find((name) => next[name]);
    if (firstInvalid) {
      focusNext.current = firstInvalid;
      setErrors(next);
      return;
    }

    setSubmittedEmail(email);
    onSubmit({ email, firstName, lastName, roles });
  }

  return (
    <section className="owner-staff-edit owner-staff-add" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>{t("ownerStaff.add.title")}</h3>
      {state.kind !== "created" && (
        <form ref={form} noValidate onSubmit={submit} data-testid="owner-staff-add-form">
          <p id={`${id}-hint`}>{t("ownerStaff.add.hint")}</p>
          <fieldset className="owner-staff-add-fields" disabled={locked} aria-describedby={`${id}-hint`}>
            <div className="owner-staff-add-identity">
              {identityFields.map(({ name, type, autoComplete }) => {
                const inputId = `${id}-${name}`;
                const isDuplicate = name === "email" && duplicate;
                const message = isDuplicate
                  ? t("ownerStaff.add.duplicate")
                  : errors[name] && t(`ownerStaff.add.${errors[name]}`);
                return (
                  <div className="pc-field" key={name}>
                    <label className="pc-field__label" htmlFor={inputId}>
                      {t(`ownerStaff.add.${name}`)}
                    </label>
                    <input
                      id={inputId}
                      name={name}
                      type={type}
                      autoComplete={autoComplete}
                      required
                      value={identity[name]}
                      aria-invalid={message ? true : undefined}
                      aria-describedby={message ? `${inputId}-error` : undefined}
                      onChange={(event) => {
                        const value = event.currentTarget.value;
                        setIdentity((current) => ({ ...current, [name]: value }));
                        clearError(name);
                      }}
                    />
                    {message && (
                      <p
                        id={`${inputId}-error`}
                        className="pc-error"
                        role="alert"
                        data-testid={isDuplicate ? "owner-staff-add-duplicate" : `owner-staff-add-error-${name}`}
                      >
                        {message}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
            <fieldset
              className="owner-staff-choices"
              aria-invalid={rolesInvalid || undefined}
              aria-describedby={`${id}-roles-help`}
            >
              <legend>{t("ownerStaff.roleChoices")}</legend>
              <p
                id={`${id}-roles-help`}
                className={rolesInvalid ? "pc-error" : "pc-field__hint"}
                role={rolesInvalid ? "alert" : undefined}
                data-testid="owner-staff-add-roles-help"
              >
                {t(`ownerStaff.add.${rolesHelp}`)}
              </p>
              <div className="owner-staff-choice-grid">
                {choices.map(({ role, label }) => (
                  <label className="owner-staff-choice" key={role}>
                    <input
                      type="checkbox"
                      name="roles"
                      value={role}
                      aria-describedby={rolesInvalid ? `${id}-roles-help` : undefined}
                      onChange={() => clearError("roles")}
                    />
                    <bdi>{label}</bdi>
                  </label>
                ))}
              </div>
            </fieldset>
          </fieldset>
          {failure && (
            <p className="owner-staff-edit-alert" role="alert" data-testid={`owner-staff-add-${failure}`}>
              {t(`ownerStaff.add.${failure}`)}
            </p>
          )}
          <div className="owner-staff-page-buttons">
            <Button
              variant="primary"
              type="submit"
              disabled={locked || duplicate}
              data-testid="owner-staff-add-submit"
            >
              {t("ownerStaff.add.submit")}
            </Button>
            {failure === "uncertain" && (
              <Button onClick={onReload} type="button" data-testid="owner-staff-add-reload">
                {t("ownerStaff.reloadDirectory")}
              </Button>
            )}
          </div>
        </form>
      )}
      <div role="status" aria-atomic="true" className="owner-staff-add-status">
        {pending && <p data-testid="owner-staff-add-pending">{t("ownerStaff.add.pending")}</p>}
        {receipt && (
          <div className="owner-staff-add-confirmation" data-testid="owner-staff-add-created">
            <p>
              <strong>{t("ownerStaff.add.created")}</strong>{" "}
              <Badge tone="success">{t("ownerStaff.enabled")}</Badge>
            </p>
            <p><bdi>{receipt.email}</bdi></p>
            <p data-testid="owner-staff-add-created-roles">
              <strong>{t("ownerStaff.add.assignedRoles")}</strong>{" "}
              {receipt.roles.length ? <bdi>{receipt.roles.join(", ")}</bdi> : t("ownerStaff.none")}
            </p>
            <p>{t("ownerStaff.add.passwordSetup")}</p>
          </div>
        )}
      </div>
    </section>
  );
}
