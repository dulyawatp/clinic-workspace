import { useReadQuery } from "../../transport/read/query";

export interface PatientSummary {
  id: string;
  hn: string;
  fullName: string;
  phone: string;
  lastVisit: string;
}

export const SEARCH_PAGE_SIZE = 20;

/** Translate UI pagination to existing proxy limit/offset contract. */
export function usePatientSearch(q: string, page: number) {
  return useReadQuery<PatientSummary>("patients", {
    q,
    limit: SEARCH_PAGE_SIZE,
    offset: (page - 1) * SEARCH_PAGE_SIZE,
  });
}
