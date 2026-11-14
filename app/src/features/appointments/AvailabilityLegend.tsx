import { useTranslation } from "react-i18next";

export type SlotState = "available" | "booked" | "blocked" | "leave" | "break";
export const SLOT_STATES: readonly SlotState[] = [
  "available",
  "booked",
  "blocked",
  "leave",
  "break",
];

export function AvailabilityLegend() {
  const { t } = useTranslation();
  return (
    <ul className="appointment-legend" aria-label={t("appointment.legend")}>
      {SLOT_STATES.map((state) => (
        <li key={state}>
          <span className={`appointment-dot appointment-dot--${state}`} />
          {t(`appointment.${state}`)}
        </li>
      ))}
    </ul>
  );
}
