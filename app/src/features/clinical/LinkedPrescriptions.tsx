import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Badge, Button } from "../../design/components";
import type { EncounterReadError } from "../../transport/native/encounter-read";
import {
  type LinkedMedication,
  type LinkedMedications,
  type MedicationChanges,
  type PrescriptionChoice,
  type PrescriptionMaster,
  searchPrescriptionMaster,
} from "../../transport/native/linked-encounter";

const doseFields = [
  ["dosage", "Prescription Dosage"],
  ["period", "Prescription Duration"],
  ["dosage_form", "Dosage Form"],
] as const;
type DoseField = typeof doseFields[number][0];
type DraftValues = Record<DoseField | "drug_code", PrescriptionChoice | null>;
export type PrescriptionDraft = { id: string | null; values: DraftValues };
const empty = { drug_code: null, dosage: null, period: null, dosage_form: null };
const fromId = (id: string): PrescriptionChoice => ({ id, label: id });

export function prescriptionChange(draft: PrescriptionDraft, medications: LinkedMedications): {
  invalid: boolean;
  changes?: MedicationChanges;
} {
  const { drug_code, dosage, period, dosage_form } = draft.values;
  if (medications.state !== "available" || !drug_code || !dosage || !period || !dosage_form) return { invalid: true };
  const dose = { dosage: dosage.id, period: period.id, dosage_form: dosage_form.id };
  if (draft.id !== null) {
    const original = medications.items.find((row) => row.id === draft.id);
    if (!original?.supported || original.drug_code !== drug_code.id || original.medication_request) return { invalid: true };
    if (doseFields.every(([field]) => original[field] === dose[field])) return { invalid: false };
    return { invalid: false, changes: { add: [], edit: [{ id: draft.id, ...dose }] } };
  }
  return { invalid: false, changes: { add: [{ drug_code: drug_code.id, ...dose }], edit: [] } };
}

function choiceText(choice: PrescriptionChoice): string {
  return choice.id === choice.label ? choice.label : `${choice.label} · ${choice.id}`;
}

function MasterPicker({ field, doctype, value, locked, onSelect }: {
  field: keyof DraftValues;
  doctype: PrescriptionMaster;
  value: PrescriptionChoice | null;
  locked: boolean;
  onSelect: (choice: PrescriptionChoice | null) => void;
}) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const label = useId();
  const [input, setInput] = useState("");
  const [settled, setSettled] = useState("");
  const search = input.trim();
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const query = useQuery<PrescriptionChoice[], EncounterReadError>({
    queryKey: ["prescription-master", authGeneration, doctype, settled],
    queryFn: ({ signal }) => searchPrescriptionMaster(doctype, settled, signal),
    enabled: !locked && Boolean(settled) && settled === search,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const kind = query.error?.code;
  return (
    <div className="pc-field visit-preview-stack">
      <label htmlFor={label}>{t(`linkedRx.${field}`)}</label>
      {value && <p className="pc-note">{choiceText(value)}</p>}
      <input id={label} type="search" disabled={locked} placeholder={t("linkedRx.search")}
        value={input} maxLength={100} onChange={(event) => {
          setInput(event.target.value);
          if (value) onSelect(null);
        }} />
      {!locked && search && (
        settled !== search || query.isPending || query.isFetching
          ? <p className="pc-note" role="status">{t("common.loading")}</p>
          : query.isError
            ? <p className="pc-note" role="status">{t(kind === "FORBIDDEN" || kind === "AUTH_EXPIRED" ? "linkedRx.lookupDenied" : "linkedRx.lookupUnavailable")}</p>
            : query.data?.length
              ? <ul className="visit-preview-choices">
                {query.data.map((choice) => <li key={choice.id}>
                  <button type="button" onClick={() => { onSelect(choice); setInput(""); }}>{choiceText(choice)}</button>
                </li>)}
              </ul>
              : <p className="pc-note" role="status">{t("linkedRx.noMatches")}</p>
      )}
    </div>
  );
}

function PrescriptionDetails({ row }: { row: LinkedMedication }) {
  const { t } = useTranslation();
  const details = row.details;
  const quantity = (value: number | null, unit: string | null) =>
    (value || unit) ? [value, unit].filter((part) => part !== null && part !== "").join(" ") : null;
  const fields: [string, string | number | null][] = [
    ["dosage", row.dosage || null], ["period", row.period || null], ["dosage_form", row.dosage_form || null],
    ["comment", details.comment || null],
    ["interval", quantity(details.interval, details.interval_uom)],
    ["strength", quantity(details.strength, details.strength_uom)],
    ["repeats", details.number_of_repeats_allowed || null],
    ["intent", details.intent || null], ["priority", details.priority || null],
    ["byInterval", details.dosage_by_interval ? t("linkedRx.enabled") : null],
    ["schedule", details.update_schedule === 0 || details.update_schedule === false ? t("linkedRx.disabled") : null],
    ["request", row.medication_request || null],
  ];
  return (
    <dl className="visit-prescription-facts">
      {fields.filter(([, value], index) => index < 3 || value !== null).map(([key, value]) => (
        <div key={key}><dt>{t(`linkedRx.${key}`)}</dt><dd>{value ?? t("common.unavailable")}</dd></div>
      ))}
    </dl>
  );
}

export function PrescriptionRecovery({ draft }: { draft: PrescriptionDraft }) {
  const { t } = useTranslation();
  return (
    <section className="visit-preview-stack">
      <h4>{t("linkedRx.pending")}</h4>
      {draft.id && <p className="pc-note">{draft.id}</p>}
      <dl className="visit-prescription-facts">
        {(["drug_code", ...doseFields.map(([field]) => field)] as const).map((field) => (
          <div key={field}><dt>{t(`linkedRx.${field}`)}</dt>
            <dd>{draft.values[field] ? choiceText(draft.values[field]) : t("common.unavailable")}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function LinkedPrescriptions({ medications, draft, editable, locked, onChange }: {
  medications: LinkedMedications;
  draft: PrescriptionDraft | null;
  editable: boolean;
  locked: boolean;
  onChange: (draft: PrescriptionDraft | null) => void;
}) {
  const { t } = useTranslation();
  if (medications.state === "unavailable") return <p className="pc-note">{t("linkedRx.unavailable")}</p>;

  function beginEdit(row: LinkedMedication) {
    const { id, drug_code, dosage, period, dosage_form } = row;
    if (!row.supported || row.medication_request || !drug_code || !dosage || !period || !dosage_form) return;
    onChange({ id, values: { drug_code: fromId(drug_code), dosage: fromId(dosage),
      period: fromId(period), dosage_form: fromId(dosage_form) } });
  }
  function select(field: keyof DraftValues, choice: PrescriptionChoice | null) {
    if (draft) onChange({ ...draft, values: { ...draft.values, [field]: choice } });
  }

  return (
    <div className="visit-preview-stack">
      {!medications.complete && <p role="status" className="pc-note">{t("linkedRx.truncated")}</p>}
      {medications.complete && !medications.items.length && <p className="pc-note">{t("linkedRx.empty")}</p>}
      {medications.items.map((row) => (
        <article className="visit-preview-stack visit-prescription-row" key={row.id}>
          <div className="visit-preview-head">
            <h4>{row.drug_code || t("linkedRx.itemUnavailable")}</h4>
            {row.medication_request && <Badge tone="success">{t("linkedRx.issued")}</Badge>}
          </div>
          <PrescriptionDetails row={row} />
          {!row.medication_request && <p className="pc-note">{t("linkedRx.noRequest")}</p>}
          {!row.supported && !row.medication_request && <p className="pc-note">{t("linkedRx.unsupported")}</p>}
          {editable && row.supported && <div>
            <Button disabled={locked || draft !== null} onClick={() => beginEdit(row)}>
              {t("linkedRx.edit", { id: row.id })}
            </Button>
          </div>}
        </article>
      ))}
      {editable ? draft ? (
        <fieldset className="visit-preview-stack visit-prescription-draft">
          <legend>{t(draft.id === null ? "linkedRx.new" : "linkedRx.editTitle")}</legend>
          {draft.id === null
            ? <MasterPicker field="drug_code" doctype="Item" value={draft.values.drug_code} locked={locked}
              onSelect={(choice) => select("drug_code", choice)} />
            : <p className="pc-note">{t("linkedRx.fixedItem", { item: draft.values.drug_code?.id })}</p>}
          {doseFields.map(([field, doctype]) => (
            <MasterPicker key={field} field={field} doctype={doctype} value={draft.values[field]} locked={locked}
              onSelect={(choice) => select(field, choice)} />
          ))}
          {draft.id === null && <p className="pc-note">{t("linkedRx.mappingHint")}</p>}
          <p className="pc-note">{t("linkedRx.draftHint")}</p>
          <div><Button disabled={locked} onClick={() => onChange(null)}>{t("linkedRx.cancelEdit")}</Button></div>
        </fieldset>
      ) : <div><Button disabled={locked} onClick={() => onChange({ id: null, values: empty })}>
        {t("linkedRx.add")}
      </Button></div> : draft && <PrescriptionRecovery draft={draft} />}
    </div>
  );
}
