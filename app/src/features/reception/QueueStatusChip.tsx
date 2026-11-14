import { useTranslation } from "react-i18next";
import { mapQueueStatus } from "../../domain/queue";
import { StatusChip } from "../../ui/StatusChip";
import { StatusMappingNote } from "./StatusMappingNote";

export function QueueStatusChip({ raw }: { raw: string }) {
  const { t } = useTranslation();
  const status = mapQueueStatus(raw);
  return (
    <>
      <StatusChip
        tone="neutral"
        label={status.labelKey ? t(status.labelKey) : raw}
      />
      {status.labelKey === null && (
        <StatusMappingNote raw={raw} source="frontdesk-queue.status" />
      )}
    </>
  );
}
