// biome-ignore-all lint/a11y/noNoninteractiveElementToInteractiveRole: This table implements managed grid focus and keyboard navigation.
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";
import type { SlotState } from "./AvailabilityLegend";

export interface Slot {
  id: string;
  date: string;
  time: string;
  practitionerId: string;
  practitionerName: string;
  state: SlotState;
  appointmentId?: string;
  appointmentType?: string;
}

function datesFor(date: string, count: number): string[] {
  const start = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(start.valueOf())) return [];
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(start);
    day.setUTCDate(day.getUTCDate() + index);
    return day.toISOString().slice(0, 10);
  });
}

export function CalendarGrid({
  view,
  date,
  practitionerId,
  appointmentType,
}: {
  view: "day" | "week" | "list";
  date: string;
  practitionerId?: string;
  appointmentType?: string;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Slot>();
  const [page, setPage] = useState(0);
  const [focusDay, setFocusDay] = useState(0);
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const dayRefs = useRef<Array<HTMLTableCellElement | null>>([]);
  const dates = datesFor(date, view === "day" ? 1 : 7);
  const filters: Record<string, string> = {
    dateFrom: dates[0] ?? "",
    dateTo: dates.at(-1) ?? "",
  };
  if (practitionerId) filters.practitionerId = practitionerId;
  if (appointmentType) filters.appointmentType = appointmentType;
  const query = useReadQuery<Slot>("frontdesk-availability", {
    filters,
    limit: 100,
    offset: page * 100,
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
  const slots = query.data.items;
  const partial = query.data.total > 100;
  function moveDay(event: KeyboardEvent<HTMLTableCellElement>, index: number) {
    if (event.target !== event.currentTarget) {
      const buttons = [...event.currentTarget.querySelectorAll("button")];
      const current = buttons.indexOf(event.target as HTMLButtonElement);
      if (event.key === "Escape") {
        event.preventDefault();
        event.currentTarget.focus();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        buttons[
          Math.max(
            0,
            Math.min(
              buttons.length - 1,
              current + (event.key === "ArrowDown" ? 1 : -1),
            ),
          )
        ]?.focus();
      }
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.querySelector("button")?.focus();
      return;
    }
    let next = index;
    if (event.key === "ArrowRight")
      next = Math.min(index + 1, dates.length - 1);
    else if (event.key === "ArrowLeft") next = Math.max(index - 1, 0);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = dates.length - 1;
    else return;
    event.preventDefault();
    setFocusDay(next);
    dayRefs.current[next]?.focus();
  }
  function slotButton(slot: Slot, grid: boolean) {
    return (
      <button
        type="button"
        tabIndex={grid ? -1 : undefined}
        data-state={slot.state}
        aria-label={`${slot.date} ${slot.practitionerName} ${slot.time} ${t(`appointment.${slot.state}`)}`}
        onClick={() => setSelected(slot)}
      >
        <strong>{slot.time}</strong> <span>{slot.practitionerName}</span> ·{" "}
        {t(`appointment.${slot.state}`)}
        {slot.appointmentType && <> · {slot.appointmentType}</>}
      </button>
    );
  }
  function dayContent(day: string, grid: boolean) {
    const rows = slots.filter((slot) => slot.date === day);
    return rows.length ? (
      <ol className="appointment-slots">
        {rows.map((slot) => (
          <li key={slot.id}>{slotButton(slot, grid)}</li>
        ))}
      </ol>
    ) : (
      <p>{t(partial ? "appointment.emptyPage" : "appointment.emptyDay")}</p>
    );
  }
  return (
    <>
      {view !== "list" && !mobile ? (
        <div className="appointment-desktop-grid">
          <p>{t("appointment.gridHelp")}</p>
          <table
            role="grid"
            aria-label={t("appointment.calendar")}
            data-view={view}
            className="appointment-grid"
          >
            <tbody>
              <tr>
                {dates.map((day, index) => (
                  <td
                    key={day}
                    role="gridcell"
                    tabIndex={focusDay === index ? 0 : -1}
                    ref={(node) => {
                      dayRefs.current[index] = node;
                    }}
                    onFocus={() => setFocusDay(index)}
                    onKeyDown={(event) => moveDay(event, index)}
                  >
                    <h3>{day}</h3>
                    {dayContent(day, true)}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <section
          className="appointment-agenda"
          data-view={view}
          aria-label={t("appointment.agenda")}
        >
          {dates.map((day) => (
            <section className="appointment-agenda-day" key={day}>
              <h3>{day}</h3>
              {dayContent(day, false)}
            </section>
          ))}
        </section>
      )}
      {partial && (
        <nav
          aria-label={t("appointment.pages")}
          className="appointment-pagination"
        >
          <button
            type="button"
            disabled={page === 0}
            onClick={() => {
              setPage(page - 1);
              setSelected(undefined);
            }}
          >
            {t("appointment.previous")}
          </button>
          <span>
            {t("appointment.pageOf", {
              page: page + 1,
              pages: Math.ceil(query.data.total / 100),
            })}
          </span>
          <button
            type="button"
            disabled={(page + 1) * 100 >= query.data.total}
            onClick={() => {
              setPage(page + 1);
              setSelected(undefined);
            }}
          >
            {t("appointment.next")}
          </button>
        </nav>
      )}
      {selected && (
        <aside className="appointment-selection" aria-live="polite">
          <h3>{t("appointment.slotDetails")}</h3>
          <p>
            {selected.date} · {selected.time} · {selected.practitionerName}
          </p>
          <p>{t(`appointment.${selected.state}`)}</p>
          {selected.appointmentId && (
            <Link to={ROUTES.appointment(selected.appointmentId)}>
              {t("appointment.openDetail")}
            </Link>
          )}
        </aside>
      )}
    </>
  );
}
