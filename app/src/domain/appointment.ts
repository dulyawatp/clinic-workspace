/**
 * Appointment list-row DTO. `status` is the source value: this plan never
 * relabels appointment states, only queue display concepts are mapped.
 */
export interface AppointmentSummary {
  id: string;
  patientId: string;
  patientName: string;
  practitionerId: string;
  practitionerName: string;
  startsAt: string;
  type: string;
  status: string;
  checkedInAt?: string;
  checkInEligible?: boolean;
}

export function canOfferCheckIn(row: {
  status: string;
  checkInEligible?: boolean;
  checkedInAt?: string;
}): boolean {
  return (
    row.checkInEligible === true &&
    !row.checkedInAt &&
    (row.status === "scheduled" || row.status === "arrived")
  );
}
