import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toI18nKey } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { StatePanel } from "../../ui/StatePanel";

export interface ClinicalRow {
  patientId: string;
  encounterId: string;
  section: string;
  chiefComplaint?: string;
  hpi?: string;
  past?: string;
  allergies?: string;
  findings?: string;
}
export interface Vital {
  id: string;
  patientId: string;
  encounterId: string;
  kind: string;
  value: number | null;
  unit: string;
  measuredAt: string;
  source: string;
}
export type ClinicalIds = { patientId: string; encounterId: string };

export function ClinicalRead<T extends ClinicalIds>({
  resource,
  patientId,
  encounterId,
  children,
}: ClinicalIds & { resource: string; children: (rows: T[]) => ReactNode }) {
  const { t } = useTranslation();
  const query = useReadQuery<T>(resource, {
    filters: { patientId, encounterId },
  });
  if (query.isError)
    return <StatePanel kind="error" title={t(toI18nKey(query.error))} />;
  if (query.isPending || query.isFetching || !query.isFetchedAfterMount)
    return <StatePanel kind="loading" title={t("common.loading")} />;
  const rows = query.data.items.filter(
    (row) => row.patientId === patientId && row.encounterId === encounterId,
  );
  return <>{children(rows)}</>;
}

export function AllergyValue({ value }: { value?: string }) {
  const { t } = useTranslation();
  const known: Record<string, string> = {
    unknown: "clinical.allergyUnknown",
    none: "clinical.allergyNone",
    known: "clinical.allergyKnown",
  };
  return (
    <>
      {value
        ? known[value]
          ? t(known[value])
          : value
        : t("clinical.sourceUnavailable")}
    </>
  );
}
