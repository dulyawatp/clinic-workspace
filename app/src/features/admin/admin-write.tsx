import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import { useCapabilities } from "../shell/CapabilityProvider";
import {
  CLINIC_ROLE_CODES,
  createNativeUser,
  editNativeMaster,
  MASTER_TYPES,
  type ClinicRoleCode,
  type MasterType,
  type NativeAdminWriteError,
} from "../../transport/native/admin-write";

type FieldKind = "text" | "number";

interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  maxLength?: number;
  /** Amendment A1.1: the practitioner ``user`` link may be left empty. */
  optional?: boolean;
}

interface MasterSpec {
  title: string;
  fields: FieldSpec[];
}

export const MASTER_SPECS: Record<MasterType, MasterSpec> = {
  warehouse: {
    title: "adminWrite.masterWarehouse",
    fields: [
      { name: "warehouse_name", label: "adminWrite.warehouseName", kind: "text", maxLength: 140 },
    ],
  },
  supplier: {
    title: "adminWrite.masterSupplier",
    fields: [
      { name: "supplier_name", label: "adminWrite.supplierName", kind: "text", maxLength: 140 },
    ],
  },
  item: {
    title: "adminWrite.masterItem",
    fields: [
      { name: "item_code", label: "adminWrite.itemCode", kind: "text", maxLength: 140 },
      { name: "item_name", label: "adminWrite.itemName", kind: "text", maxLength: 140 },
      { name: "item_group", label: "adminWrite.itemGroup", kind: "text", maxLength: 140 },
      { name: "stock_uom", label: "adminWrite.stockUom", kind: "text", maxLength: 140 },
      { name: "price_list", label: "adminWrite.priceList", kind: "text", maxLength: 140 },
      // The installed Item Price rate field is ``price_list_rate``; the label key
      // stays the shared "Rate" copy.
      { name: "price_list_rate", label: "adminWrite.rate", kind: "number" },
    ],
  },
  price_list: {
    title: "adminWrite.masterPriceList",
    fields: [
      { name: "price_list_name", label: "adminWrite.priceListName", kind: "text", maxLength: 140 },
    ],
  },
  mode_of_payment: {
    title: "adminWrite.masterPayment",
    fields: [
      { name: "mode_of_payment", label: "adminWrite.paymentMode", kind: "text", maxLength: 140 },
      { name: "account", label: "adminWrite.account", kind: "text", maxLength: 140 },
    ],
  },
  procedure_template: {
    title: "adminWrite.masterProcedureTemplate",
    fields: [
      { name: "template_name", label: "adminWrite.templateName", kind: "text", maxLength: 140 },
      { name: "item_code", label: "adminWrite.itemCode", kind: "text", maxLength: 140 },
      { name: "rate", label: "adminWrite.rate", kind: "number" },
    ],
  },
  uom: {
    title: "adminWrite.masterUom",
    fields: [
      { name: "uom_name", label: "adminWrite.uomName", kind: "text", maxLength: 140 },
    ],
  },
  supplier_group: {
    title: "adminWrite.masterSupplierGroup",
    fields: [
      { name: "supplier_group_name", label: "adminWrite.supplierGroupName", kind: "text", maxLength: 140 },
    ],
  },
  practitioner: {
    title: "adminWrite.masterPractitioner",
    fields: [
      { name: "practitioner_name", label: "adminWrite.practitionerName", kind: "text", maxLength: 140 },
      {
        name: "user",
        label: "adminWrite.practitionerUser",
        kind: "text",
        maxLength: 140,
        optional: true,
      },
    ],
  },
};

function failureKey(error: unknown): string {
  const code = (error as NativeAdminWriteError | null)?.code;
  if (code === "validation") return "adminWrite.validation";
  if (code === "forbidden") return "adminWrite.forbidden";
  if (code === "conflict") return "adminWrite.conflict";
  if (code === "auth") return "adminWrite.auth";
  if (code === "locked" || code === "uncertain") return "adminWrite.uncertain";
  return "adminWrite.uncertain";
}

function newOperationKey(): string {
  return crypto.randomUUID();
}

/**
 * ADM-02 create-user surface. Rendered only when the deployment advertises the
 * admin user capability; the credential is typed by the operator and never
 * stored by this component.
 */
export function AdminUserWriteForm() {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [roleCode, setRoleCode] = useState<ClinicRoleCode>("PHA");
  const [practitioner, setPractitioner] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (get("write.admin.user") !== "enabled") return null;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    if (
      !email.trim() ||
      !fullName.trim() ||
      password.length < 12 ||
      !CLINIC_ROLE_CODES.includes(roleCode)
    ) {
      setError(t("adminWrite.invalid"));
      return;
    }
    setError(null);
    setSaved(null);
    setSaving(true);
    try {
      const created = await createNativeUser({
        operation_key: newOperationKey(),
        email: email.trim(),
        full_name: fullName.trim(),
        clinic_role_code: roleCode,
        password,
        ...(practitioner.trim() ? { practitioner: practitioner.trim() } : {}),
      });
      setSaved(created.user_id);
      setPassword("");
      setOpen(false);
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="admin-write" aria-label={t("adminWrite.userTitle")}>
      {!open && (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          {t("adminWrite.createUser")}
        </Button>
      )}
      {saved && (
        <p role="status">
          {t("adminWrite.createdUser")} <span className="ops-mono">{saved}</span>
        </p>
      )}
      {error && (
        <p className="pc-error" role="alert">
          {error}
        </p>
      )}
      {open && (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <h3>{t("adminWrite.userTitle")}</h3>
          <p className="pc-note">{t("adminWrite.userNotice")}</p>
          <div className="admin-write-fields">
            <div className="admin-write-field">
              <label htmlFor={`${id}-email`}>{t("adminWrite.email")}</label>
              <input
                id={`${id}-email`}
                type="email"
                maxLength={140}
                value={email}
                required
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div className="admin-write-field">
              <label htmlFor={`${id}-name`}>{t("adminWrite.fullName")}</label>
              <input
                id={`${id}-name`}
                maxLength={140}
                value={fullName}
                required
                onChange={(event) => setFullName(event.target.value)}
              />
            </div>
            <div className="admin-write-field">
              <label htmlFor={`${id}-role`}>{t("adminWrite.roleCode")}</label>
              <select
                id={`${id}-role`}
                value={roleCode}
                onChange={(event) =>
                  setRoleCode(event.target.value as ClinicRoleCode)
                }
              >
                {CLINIC_ROLE_CODES.map((code) => (
                  <option key={code} value={code}>
                    {code} · {t(`role.${code.toLowerCase()}.short`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="admin-write-field">
              <label htmlFor={`${id}-practitioner`}>
                {t("adminWrite.practitioner")}
              </label>
              <input
                id={`${id}-practitioner`}
                maxLength={140}
                value={practitioner}
                onChange={(event) => setPractitioner(event.target.value)}
              />
            </div>
            <div className="admin-write-field">
              <label htmlFor={`${id}-password`}>{t("adminWrite.password")}</label>
              <input
                id={`${id}-password`}
                type="password"
                autoComplete="new-password"
                maxLength={128}
                minLength={12}
                value={password}
                required
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
          </div>
          <div className="admin-write-actions">
            <Button type="submit" disabled={saving}>
              {saving ? t("adminWrite.saving") : t("adminWrite.createUser")}
            </Button>
            <Button
              variant="secondary"
              onClick={() => setOpen(false)}
              disabled={saving}
            >
              {t("action.close")}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

/**
 * ADM-03/ADM-04 master create surface for one designed DocType variant. Only
 * the variant's installed fields are shown; the company and currency are read
 * by the server.
 */
export function AdminMasterWriteForm({ masterType }: { masterType: MasterType }) {
  const { t } = useTranslation();
  const { get } = useCapabilities();
  const id = useId();
  const spec = MASTER_SPECS[masterType];
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (get("write.admin.master") !== "enabled") return null;
  if (!MASTER_TYPES.includes(masterType)) return null;

  function setField(name: string, value: string) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const payload: Record<string, string | number> = {};
    for (const field of spec.fields) {
      const raw = (values[field.name] ?? "").trim();
      if (!raw) {
        if (field.optional) {
          payload[field.name] = "";
          continue;
        }
        setError(t("adminWrite.invalid"));
        return;
      }
      if (field.kind === "number") {
        const number = Number(raw);
        if (!Number.isFinite(number) || number <= 0) {
          setError(t("adminWrite.invalid"));
          return;
        }
        payload[field.name] = number;
      } else {
        payload[field.name] = raw;
      }
    }
    setError(null);
    setSaved(null);
    setSaving(true);
    try {
      const created = await editNativeMaster({
        operation_key: newOperationKey(),
        master_type: masterType,
        payload,
      });
      setSaved(created.master_id);
      setValues({});
      setOpen(false);
    } catch (failure) {
      setError(t(failureKey(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="admin-write" aria-label={t(spec.title)}>
      {!open && (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          {t("adminWrite.createMaster", { master: t(spec.title) })}
        </Button>
      )}
      {saved && (
        <p role="status">
          {t("adminWrite.createdMaster")}{" "}
          <span className="ops-mono">{saved}</span>
        </p>
      )}
      {error && (
        <p className="pc-error" role="alert">
          {error}
        </p>
      )}
      {open && (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <h3>{t(spec.title)}</h3>
          <p className="pc-note">{t("adminWrite.masterNotice")}</p>
          <div className="admin-write-fields">
            {spec.fields.map((field) => (
              <div className="admin-write-field" key={field.name}>
                <label htmlFor={`${id}-${field.name}`}>{t(field.label)}</label>
                <input
                  id={`${id}-${field.name}`}
                  type={field.kind === "number" ? "number" : "text"}
                  maxLength={field.kind === "number" ? undefined : field.maxLength}
                  min={field.kind === "number" ? 0 : undefined}
                  step={field.kind === "number" ? "0.01" : undefined}
                  value={values[field.name] ?? ""}
                  required={!field.optional}
                  onChange={(event) =>
                    setField(field.name, event.target.value)
                  }
                />
              </div>
            ))}
          </div>
          <div className="admin-write-actions">
            <Button type="submit" disabled={saving}>
              {saving ? t("adminWrite.saving") : t("adminWrite.createMaster", { master: t(spec.title) })}
            </Button>
            <Button
              variant="secondary"
              onClick={() => setOpen(false)}
              disabled={saving}
            >
              {t("action.close")}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
