import { type FormEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button, Card } from "../../design/components";
import {
  lookupNativeOwnerHours,
  OWNER_HOURS_DAYS,
  OWNER_HOURS_LOOKUP_LIMIT,
  OWNER_HOURS_MAX_ROWS,
  type OwnerHoursLookup,
  type OwnerHoursSaveResult,
  type OwnerHoursSlot,
  stageNativeOwnerHours,
} from "../../transport/native/owner-doctor-hours";

const fields = [
  ["from_time", "start", "time"],
  ["to_time", "end", "time"],
  ["duration", "duration", "number"],
  ["maximum_appointments", "capacity", "number"],
] as const;
type SlotRow = { id: number; day: OwnerHoursSlot["day"] } &
  Record<(typeof fields)[number][0], string>;

function emptyRow(id: number): SlotRow {
  return {
    id, day: "Monday", from_time: "", to_time: "", duration: "", maximum_appointments: "",
  };
}

function minutes(value: string): number {
  if (!/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value)) return NaN;
  return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
}

function readSlot({ day, from_time, to_time, duration, maximum_appointments }: SlotRow) {
  return {
    start: minutes(from_time),
    end: minutes(to_time),
    slot: { day, from_time, to_time, duration: Number(duration), maximum_appointments: Number(maximum_appointments) },
  };
}

function isValidSlot({ start, end, slot }: ReturnType<typeof readSlot>) {
  return end > start &&
    Number.isFinite(slot.duration) && slot.duration > 0 &&
    Number.isSafeInteger(slot.maximum_appointments) && slot.maximum_appointments > 0 &&
    slot.duration * slot.maximum_appointments <= end - start;
}

function prepareSlots(rows: SlotRow[]) {
  const ordered = rows
    .map((draft, index) => ({
      row: index + 1,
      dayIndex: OWNER_HOURS_DAYS.indexOf(draft.day),
      ...readSlot(draft),
    }))
    .sort((a, b) => a.dayIndex - b.dayIndex || a.start - b.start);
  for (const [index, entry] of ordered.entries()) {
    const { row, dayIndex, start } = entry;
    if (!isValidSlot(entry)) {
      return { issue: { kind: "invalid", row } } as const;
    }
    const previous = ordered[index - 1];
    if (previous?.dayIndex === dayIndex && start < previous.end) {
      return { issue: { kind: "overlap", row, other: previous.row } } as const;
    }
  }
  return { slots: ordered.map(({ slot }) => slot) } as const;
}

const previewAxis = ["00:00", "12:00", "24:00"];
const minutesPerDay = 24 * 60;

function WeeklyHoursPreview({ rows, reviewed = false }: { rows: SlotRow[]; reviewed?: boolean }) {
  const { t } = useTranslation();
  const titleId = useId();
  const enteredRows = rows.filter((row) => fields.some(([field]) => row[field] !== ""));
  const valid = enteredRows.map(readSlot).filter(isValidSlot);
  const visible = valid.filter((entry) => !valid.some((other) =>
    other !== entry && other.slot.day === entry.slot.day &&
    entry.start < other.end && other.start < entry.end,
  )).sort((a, b) => a.start - b.start);

  return (
    <section className="owner-hours-preview" aria-labelledby={titleId} data-testid="owner-hours-preview">
      <h3 id={titleId}>{t(reviewed ? "ownerDoctor.finish.reviewedHours" : "ownerHours.preview.title")}</h3>
      <p className="pc-note">{t(reviewed ? "ownerDoctor.finish.reviewedHint" : "ownerHours.preview.hint")}</p>
      {visible.length !== enteredRows.length && (
        <p className="pc-note" data-testid="owner-hours-preview-omitted">{t("ownerHours.preview.omitted")}</p>
      )}
      <div className="owner-hours-preview-grid" aria-hidden="true">
        <div className="owner-hours-preview-axis">
          {previewAxis.map((time, index) => (
            <span key={time} style={{ left: `${index * 50}%` }}>{time}</span>
          ))}
        </div>
      </div>
      <ul className="owner-hours-preview-days" role="list">
        {OWNER_HOURS_DAYS.map((day) => {
          const blocks = visible.filter(({ slot }) => slot.day === day);
          return (
            <li className="owner-hours-preview-grid" key={day} data-testid="owner-hours-preview-day" data-day={day}>
              <span className="owner-hours-preview-day">{t(`ownerHours.day.${day}`)}</span>
              <div className="owner-hours-preview-hours">
                <div className="owner-hours-preview-track" aria-hidden="true">
                  {blocks.map(({ start, end }) => (
                    <span
                      className="owner-hours-preview-bar"
                      key={start}
                      data-testid="owner-hours-preview-bar"
                      style={{ left: `${start / minutesPerDay * 100}%`, width: `${(end - start) / minutesPerDay * 100}%` }}
                    />
                  ))}
                </div>
                <p className="owner-hours-preview-times">{blocks.length
                  ? blocks.map(({ slot }) => reviewed
                    ? t("ownerDoctor.finish.slot", { start: slot.from_time, end: slot.to_time, duration: slot.duration, capacity: slot.maximum_appointments })
                    : `${slot.from_time}–${slot.to_time}`).join(reviewed ? "; " : ", ")
                  : t("ownerHours.preview.empty")}</p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function ReviewedWorkingHours({ slots }: { slots: OwnerHoursSlot[] }) {
  return <WeeklyHoursPreview reviewed rows={slots.map((slot, id) => ({
    ...slot, id, duration: String(slot.duration), maximum_appointments: String(slot.maximum_appointments),
  }))} />;
}

type SaveState = OwnerHoursSaveResult | { kind: "idle" | "pending" };

type DraftWorkingHoursProps = {
  profile?: { name: string; displayName: string };
  hidden?: boolean;
  onPendingChange?: (pending: boolean) => void;
  onSaved?: () => void;
};

export function DraftWorkingHours({ profile, hidden, onPendingChange, onSaved }: DraftWorkingHoursProps = {}) {
  const { t } = useTranslation();
  const [lookup, setLookup] = useState<OwnerHoursLookup | { kind: "loading" }>({ kind: "loading" });
  const [request, setRequest] = useState(0);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [pickerValue, setPractitioner] = useState("");
  const practitioner = profile?.name ?? pickerValue;
  const [unit, setUnit] = useState("");
  const [rows, setRows] = useState(() => [emptyRow(1)]);
  const [issue, setIssue] = useState<ReturnType<typeof prepareSlots>["issue"]>();
  const nextRow = useRef(2);
  const saveLocked = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const newRowFocus = useRef<number>();

  useEffect(() => {
    if (request > 0) feedbackRef.current?.focus();
    const controller = new AbortController();
    void lookupNativeOwnerHours(controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) setLookup(result);
      },
      () => {
        if (!controller.signal.aborted) setLookup({ kind: "error" });
      },
    );
    return () => controller.abort();
  }, [request]);

  useLayoutEffect(() => {
    const row = rows.find(({ id }) => id === newRowFocus.current);
    if (!row) return;
    formRef.current?.querySelector<HTMLElement>(
      `[data-hours-row="${row.id}"] select`,
    )?.focus();
    newRowFocus.current = undefined;
  }, [rows]);

  useEffect(() => {
    if (issue || (save.kind !== "idle" && save.kind !== "pending")) {
      feedbackRef.current?.focus();
    }
  }, [issue, save.kind]);

  function reload() {
    saveLocked.current = false;
    setPractitioner("");
    setUnit("");
    setRows([emptyRow(nextRow.current++)]);
    setIssue(undefined);
    setSave({ kind: "idle" });
    setLookup({ kind: "loading" });
    setRequest((count) => count + 1);
  }

  function updateRow(id: number, patch: Partial<Omit<SlotRow, "id">>) {
    clearValidation();
    setRows((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row));
  }

  function clearValidation() {
    setIssue(undefined);
    if (save.kind === "validation") setSave({ kind: "idle" });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saveLocked.current || lookup.kind !== "ready" || !canEdit) return;
    const selected = lookup.options.practitioners.find(({ name }) => name === practitioner);
    if (!selected || !lookup.options.serviceUnits.some(({ name }) => name === unit)) return;
    const prepared = prepareSlots(rows);
    if (prepared.issue) {
      setIssue(prepared.issue);
      return;
    }
    setIssue(undefined);
    saveLocked.current = true;
    setSave({ kind: "pending" });
    onPendingChange?.(true);
    const result = await stageNativeOwnerHours({
      practitioner, version: selected.version, serviceUnit: unit, slots: prepared.slots,
    });
    // Only a definitive rejection permits resubmitting this same draft.
    if (result.kind === "validation") saveLocked.current = false;
    setSave(result);
    onPendingChange?.(false);
    if (result.kind === "saved") onSaved?.();
  }

  if (hidden || (!profile && (lookup.kind === "unavailable" || (lookup.kind === "loading" && request === 0)))) return null;

  const hasPractitioners = lookup.kind === "ready" && (profile
    ? lookup.options.practitioners.some(({ name }) => name === profile.name)
    : lookup.options.practitioners.length > 0);
  const hasUnits = lookup.kind === "ready" && lookup.options.serviceUnits.length > 0;
  const pending = save.kind === "pending";
  const canEdit = hasPractitioners && hasUnits &&
    (save.kind === "idle" || pending || save.kind === "validation");
  const canReload = lookup.kind === "error" || save.kind === "stale" || save.kind === "uncertain" ||
    save.kind === "saved" || (lookup.kind === "ready" && (!hasPractitioners || !hasUnits));
  const error = issue ? t(`ownerHours.${issue.kind}`, issue)
    : save.kind !== "idle" && !pending && save.kind !== "saved" ? t(`ownerHours.save.${save.kind}`)
      : lookup.kind === "error" || lookup.kind === "expired" || lookup.kind === "unavailable" ? t(`ownerHours.lookup.${lookup.kind}`)
        : "";

  return (
    <Card title={t("ownerHours.title")} hint={t(profile ? "ownerDoctor.hoursHint" : "ownerHours.hint")} className="owner-hours-card">
      <div data-testid="owner-hours" className="owner-hours">
        <div ref={feedbackRef} tabIndex={-1} className="owner-hours-feedback">
          {error && (
            <p role="alert" className="owner-staff-edit-alert" data-testid="owner-hours-error">{error}</p>
          )}
          <div role="status" aria-atomic="true">
            {lookup.kind === "loading" && <p>{t("ownerHours.loading")}</p>}
            {pending && <p>{t("ownerHours.saving")}</p>}
            {lookup.kind === "ready" && (!hasPractitioners || !hasUnits) && (
              <div data-testid="owner-hours-empty">
                {!hasPractitioners && <p>{t(profile ? "ownerDoctor.hoursIneligible" : "ownerHours.noPractitioners")}</p>}
                {!hasUnits && <p>{t("ownerHours.noUnits")}</p>}
              </div>
            )}
            {save.kind === "saved" && (
              <div className="owner-hours-receipt" data-testid="owner-hours-saved">
                <div><strong>{t("ownerHours.saved")}</strong> <Badge tone="neutral">{t("ownerStaff.disabled")}</Badge></div>
                <p>{t("ownerHours.savedDetail", { practitioner: profile?.displayName ?? save.receipt.practitioner })}</p>
                <p className="pc-note">{t("ownerHours.schedule")} <bdi>{save.receipt.schedule}</bdi></p>
              </div>
            )}
            {lookup.kind === "ready" && (canEdit || !hasPractitioners || !hasUnits) && (
              <p className="pc-note">{t("ownerHours.lookupLimit", { limit: OWNER_HOURS_LOOKUP_LIMIT })}</p>
            )}
          </div>
        </div>
        {canReload && (
          <Button data-testid="owner-hours-reload" onClick={reload}>
            {t(profile ? "ownerDoctor.checkHours" : "ownerHours.reload")}
          </Button>
        )}
        {canEdit && lookup.kind === "ready" && (
          <form ref={formRef} onSubmit={(event) => void submit(event)} data-testid="owner-hours-form">
            <fieldset className="owner-hours-fields" disabled={pending} aria-label={t("ownerHours.title")}>
              <div className="pc-field-grid">
                {!profile && <label className="pc-field">
                  <span className="pc-field__label">{t("ownerHours.practitioner")}</span>
                  <select required value={practitioner} onChange={(event) => {
                    clearValidation();
                    setPractitioner(event.target.value);
                  }} data-testid="owner-hours-practitioner">
                    <option value="">{t("ownerHours.choosePractitioner")}</option>
                    {lookup.options.practitioners.map(({ name }) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>}
                <label className="pc-field">
                  <span className="pc-field__label">{t("ownerHours.unit")}</span>
                  <select required value={unit} onChange={(event) => {
                    clearValidation();
                    setUnit(event.target.value);
                  }} data-testid="owner-hours-unit">
                    <option value="">{t("ownerHours.chooseUnit")}</option>
                    {lookup.options.serviceUnits.map(({ name }) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
              </div>
              <h3>{t("ownerHours.weekly")}</h3>
              <p className="pc-note">{t("ownerHours.rowHint", { limit: OWNER_HOURS_MAX_ROWS })}</p>
              {rows.map((row, index) => (
                <fieldset className="owner-hours-row" key={row.id} data-testid="owner-hours-row" data-hours-row={row.id}>
                  <legend>{t("ownerHours.row", { row: index + 1 })}</legend>
                  <div className="owner-hours-row-fields">
                    <label className="pc-field">
                      <span className="pc-field__label">{t("ownerHours.day")}</span>
                      <select value={row.day} onChange={(event) => updateRow(row.id, { day: event.target.value as SlotRow["day"] })} data-testid="owner-hours-day">
                        {OWNER_HOURS_DAYS.map((day) => <option key={day} value={day}>{t(`ownerHours.day.${day}`)}</option>)}
                      </select>
                    </label>
                    {fields.map(([field, label, type]) => (
                      <label className="pc-field" key={field}>
                        <span className="pc-field__label">{t(`ownerHours.${label}`)}</span>
                        <input
                          type={type}
                          required
                          min={field === "maximum_appointments" ? 1 : undefined}
                          step={field === "duration" ? "any" : undefined}
                          value={row[field]}
                          onChange={(event) => updateRow(row.id, { [field]: event.target.value })}
                          data-testid={`owner-hours-${label}`}
                        />
                      </label>
                    ))}
                  </div>
                  {rows.length > 1 && (
                    <Button
                      variant="ghost"
                      data-testid="owner-hours-remove"
                      aria-label={t("ownerHours.removeRow", { row: index + 1 })}
                      onClick={() => {
                        newRowFocus.current = (rows[index + 1] ?? rows[index - 1])?.id;
                        clearValidation();
                        setRows(rows.filter(({ id }) => id !== row.id));
                      }}
                    >{t("ownerHours.remove")}</Button>
                  )}
                </fieldset>
              ))}
              <WeeklyHoursPreview rows={rows} />
              <div className="owner-hours-actions">
                <Button
                  disabled={rows.length >= OWNER_HOURS_MAX_ROWS}
                  data-testid="owner-hours-add"
                  onClick={() => {
                    const id = nextRow.current++;
                    newRowFocus.current = id;
                    clearValidation();
                    setRows([...rows, emptyRow(id)]);
                  }}
                >{t("ownerHours.add")}</Button>
                <Button variant="primary" type="submit" data-testid="owner-hours-save">
                  {t(pending ? "ownerHours.saving" : "ownerHours.save")}
                </Button>
              </div>
            </fieldset>
          </form>
        )}
      </div>
    </Card>
  );
}
