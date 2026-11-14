import { useTranslation } from "react-i18next";
import type { AppointmentSummary } from "../../domain/appointment";
import type { CapabilityMap } from "../../domain/capability";
import { WriteGate } from "../../ui/WriteGate";

export function AppointmentActions({
  appointment,
  caps: _caps,
}: {
  appointment: AppointmentSummary;
  caps: CapabilityMap;
}) {
  const { t } = useTranslation();
  const terminal = ["completed", "cancelled", "no_show"].includes(
    appointment.status,
  );
  return (
    <div className="appointment-actions">
      {["book", ...(!terminal ? ["reschedule", "cancel", "noShow"] : [])].map(
        (action) => (
          <WriteGate
            key={action}
            capability="write.appointment.book"
            reason={t("appointment.unavailable")}
          >
            <button type="button" disabled>
              {t(`appointment.${action}`)}
            </button>
          </WriteGate>
        ),
      )}
    </div>
  );
}
