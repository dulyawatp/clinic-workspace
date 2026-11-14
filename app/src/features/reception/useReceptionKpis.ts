import { useReadQuery } from "../../transport/read/query";

export type KpiRange = "today" | "week" | "month";

/** Counts are server aggregates; the client never sums rows to make them. */
export interface ReceptionKpi {
  id: string;
  key: string;
  labelKey: string;
  count: number;
  scope: string;
  date: string;
  fictional: true;
}

export function useReceptionKpis(range: KpiRange) {
  return useReadQuery<ReceptionKpi>("reception-kpis", {
    filters: { scope: range },
  });
}
