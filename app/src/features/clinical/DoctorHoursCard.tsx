import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Card } from "../../design/components";
import { type DoctorHours, readNativeDoctorHours } from "../../transport/native/doctor-hours";
import { nativeTransportEnabled } from "../../transport/native/flag";

export function DoctorHoursCard() {
  const { t } = useTranslation();
  const { status, session, authGeneration, nativeHoursAuth } = useSession();
  const isNative = nativeTransportEnabled();
  const authority =
    isNative && nativeHoursAuth === "confirmed" && status === "authenticated" && session?.mode === "live"
    && session.user.roles.length === 1 && session.user.roles[0] === "DOC"
      ? JSON.stringify([authGeneration, session.user.id, session.user.roles[0], session.sessionEpoch])
      : null;

  return (
    <Card title={t("doctor.hours")} hint={t("doctor.hoursNote")}>
      <HoursBody
        key={authority ?? status}
        canRead={authority !== null}
        checking={isNative && (status === "loading" || nativeHoursAuth === "checking")}
      />
    </Card>
  );
}

function HoursBody({ canRead, checking }: { canRead: boolean; checking: boolean }) {
  const { t } = useTranslation();
  const [hours, setHours] = useState<DoctorHours | null>(null);

  // No shared cache. Authority changes remount this body before old hours can render.
  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    void readNativeDoctorHours(controller.signal).then((result) => {
      if (!controller.signal.aborted) setHours(result);
    });
    return () => controller.abort();
  }, [canRead]);

  const state = hours?.state ?? (canRead || checking ? "loading" : "unavailable");
  return (
    <div data-testid="doctor-hours" data-state={state} aria-live="polite" aria-atomic="true">
      {hours?.state === "ready" ? (
        <div role="list">
          {hours.schedules.map((schedule, scheduleIndex) =>
            schedule.slots.map((slot, slotIndex) => (
              <div
                className="pc-listrow"
                data-testid="doctor-hours-slot"
                key={`${scheduleIndex}-${slotIndex}`}
                role="listitem"
              >
                <div className="pc-listrow__grow">
                  <strong>{t(`day.${slot.day}`)}</strong>
                  <p className="pc-listrow__meta">{slot.from_time}–{slot.to_time}</p>
                  {schedule.serviceUnit && (
                    <p className="pc-listrow__meta">{schedule.serviceUnit}</p>
                  )}
                </div>
              </div>
            )),
          )}
        </div>
      ) : (
        <p className="pc-note">{t(`doctor.hoursState.${state}`)}</p>
      )}
    </div>
  );
}
