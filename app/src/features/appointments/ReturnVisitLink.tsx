import { useTranslation } from "react-i18next";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { returnVisitSource } from "../../fixtures/data-authored";
import { RoutedControl, WriteGate } from "../../ui/WriteGate";

/** Builds the reception booking destination with its authored source hint. */
export function returnVisitHref(
  patientId: string,
  appointmentId: string,
): string {
  const params = new URLSearchParams({
    book: "1",
    patient: patientId,
    source: "return",
    appointment: appointmentId,
  });
  if (
    patientId === returnVisitSource.patientId ||
    appointmentId === returnVisitSource.sourceAppointmentId
  ) {
    params.set("returnSource", returnVisitSource.id);
    params.set("lastVisit", returnVisitSource.lastVisitId);
    params.set("suggestedType", returnVisitSource.suggestedTypeId);
  }
  return `${ROUTES.reception}?${params.toString()}`;
}

/**
 * REC-04 created-return-visit control. It only opens the existing reception
 * booking flow; it never writes an appointment itself. Reception owns booking,
 * so a non-REC role sees the truthful cross-role explanation instead of a link.
 */
export function ReturnVisitLink({
  patientId,
  appointmentId,
}: {
  patientId: string;
  appointmentId: string;
}) {
  const { t } = useTranslation();
  const { session } = useSession();
  const isReception = session?.user.roles.includes("REC") === true;
  if (!isReception)
    return (
      <p className="appointment-write-note" role="status">
        {t("appointmentWrite.returnVisitReceptionOnly")}
      </p>
    );
  return (
    <WriteGate
      capability="write.appointment.book"
      reason={t("appointmentWrite.returnVisitUnavailable")}
    >
      <RoutedControl
        className="pc-button pc-button--secondary"
        to={returnVisitHref(patientId, appointmentId)}
      >
        {t("appointment.returnVisit")}
      </RoutedControl>
    </WriteGate>
  );
}
