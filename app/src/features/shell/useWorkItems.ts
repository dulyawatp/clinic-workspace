import type { Capability } from "../../domain/capability";
import { useReadQuery } from "../../transport/read/query";

export interface WorkItem {
  id: string;
  kind: string;
  labelKey: string;
  count: number;
  routeKey?: string;
  requiredCapability?: Capability;
}

/** Server-scoped read only; never aggregate counts or infer authorization. */
export function useWorkItems() {
  return useReadQuery<WorkItem>(
    "work-items",
    {},
    {
      suppressAutomaticRefetchOnError: true,
    },
  );
}
