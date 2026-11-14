import type { PatientSummary } from "../../domain/patient";
import { useReadQuery } from "../../transport/read/query";

export const PATIENTS_PAGE_SIZE = 10;

/** Translate UI pagination to the shared proxy limit/offset contract. */
export function usePatients({
  q,
  page,
  pageSize = PATIENTS_PAGE_SIZE,
}: {
  q: string;
  page: number;
  pageSize?: number;
}) {
  return useReadQuery<PatientSummary>("frontdesk-patients", {
    q,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  });
}
