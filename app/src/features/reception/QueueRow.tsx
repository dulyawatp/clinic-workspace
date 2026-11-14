import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { canOfferCheckIn } from "../../domain/appointment";
import { getCapability } from "../../domain/capability";
import { formatDateTime } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { Button } from "../../ui/Button";
import { WriteGate } from "../../ui/WriteGate";
import { useCapabilities } from "../shell/CapabilityProvider";
import { QueueStatusChip } from "./QueueStatusChip";

export interface QueueEntry {
  id: string;
  appointmentId: string;
  patientId: string;
  patientName: string;
  practitionerName: string;
  type: string;
  startsAt: string;
  status: string;
  checkInEligible: boolean;
  checkedInAt?: string;
  arrivalAt?: string;
  waitMinutes?: number;
}

export function QueueRow({ appointment }: { appointment: QueueEntry }) {
  const { t } = useTranslation();
  const { locale } = useLocale();
  const { caps } = useCapabilities();
  const unavailable = t("queue.unavailable");
  return (
    <tr>
      <td headers="queue-header-time" data-label={t("rec.time")}>
        <time dateTime={appointment.startsAt}>
          {formatDateTime(appointment.startsAt, locale)}
        </time>
      </td>
      <td headers="queue-header-patient" data-label={t("nav.patients")}>
        <div>
          <Link
            className="reception-patient"
            to={ROUTES.patient(appointment.patientId)}
          >
            {appointment.patientName}
          </Link>
          <small>{appointment.patientId}</small>
        </div>
      </td>
      <td headers="queue-header-doctor" data-label={t("queue.doctorType")}>
        <div>
          {appointment.practitionerName}
          <small>{appointment.type}</small>
        </div>
      </td>
      <td headers="queue-header-reason" data-label={t("queue.reason")}>
        {unavailable}
      </td>
      <td headers="queue-header-arrival" data-label={t("queue.arrivalWait")}>
        {appointment.arrivalAt ? (
          <div>
            <time dateTime={appointment.arrivalAt}>
              {formatDateTime(appointment.arrivalAt, locale)}
            </time>
            <small>
              {appointment.waitMinutes === undefined
                ? unavailable
                : t("queue.waitMinutes", { count: appointment.waitMinutes })}
            </small>
          </div>
        ) : (
          unavailable
        )}
      </td>
      <td headers="queue-header-status" data-label={t("rec.status")}>
        <div>
          <QueueStatusChip raw={appointment.status} />
        </div>
      </td>
      <td headers="queue-header-next" data-label={t("queue.nextStep")}>
        <div className="queue-next-step">
          <Link to={ROUTES.appointment(appointment.appointmentId)}>
            {t("appointment.openDetail")}
          </Link>
          {appointment.checkedInAt ? (
            <small>{t("queue.checkedIn")}</small>
          ) : (
            canOfferCheckIn(appointment) && (
              <WriteGate
                capability="write.appointment.checkin"
                reason={t("queue.writeUnavailable")}
              >
                <Button
                  variant="secondary"
                  disabled
                  disabledReason={
                    getCapability(caps, "write.appointment.checkin") ===
                    "enabled"
                      ? t("queue.writeUnavailable")
                      : undefined
                  }
                >
                  {t("action.checkIn")}
                </Button>
              </WriteGate>
            )
          )}
        </div>
      </td>
    </tr>
  );
}
