/**
 * Queue statuses are display concepts mapped onto a documented source value.
 * The map is explicit: an unlisted source value renders as its raw code with
 * an annotation, never as a guessed or default label.
 */
export type QueueStatus =
  | "scheduled"
  | "arrived"
  | "waiting"
  | "called"
  | "in_consultation"
  | "completed"
  | "no_show"
  | "cancelled";

const QUEUE_LABEL_KEYS: Readonly<Record<QueueStatus, string>> = {
  scheduled: "status.scheduled",
  arrived: "status.arrived",
  waiting: "status.waiting",
  called: "status.called",
  in_consultation: "status.withDoctor",
  completed: "status.completed",
  no_show: "status.noShow",
  cancelled: "status.cancelled",
};

export const QUEUE_STATUSES = Object.keys(
  QUEUE_LABEL_KEYS,
) as readonly QueueStatus[];

export function isQueueStatus(value: unknown): value is QueueStatus {
  return typeof value === "string" && Object.hasOwn(QUEUE_LABEL_KEYS, value);
}

export interface QueueStatusDisplay {
  /** `null` means "render the raw value plus an admin annotation". */
  labelKey: string | null;
  raw: string;
}

export function mapQueueStatus(raw: string): QueueStatusDisplay {
  if (!isQueueStatus(raw)) return { labelKey: null, raw };
  return { labelKey: QUEUE_LABEL_KEYS[raw], raw };
}
