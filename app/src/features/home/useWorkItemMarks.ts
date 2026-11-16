import { useCallback, useEffect, useState } from "react";

export interface WorkItemMark {
  read: boolean;
  dismissed: boolean;
}

type WorkItemMarks = Record<string, WorkItemMark>;

/** Prototype parity: read/dismiss state is local to one demo identity. */
export function workItemsStorageKey(userId: string): string {
  return `dc.v3.notifications.${userId}`;
}

function parseMarks(value: unknown): WorkItemMarks {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return {};
  const marks: WorkItemMarks = {};
  for (const [id, raw] of Object.entries(value)) {
    if (!id || typeof raw !== "object" || raw === null || Array.isArray(raw))
      continue;
    const read = "read" in raw && raw.read === true;
    const dismissed = "dismissed" in raw && raw.dismissed === true;
    if (read || dismissed) marks[id] = { read, dismissed };
  }
  return marks;
}

function readMarks(userId: string): WorkItemMarks {
  try {
    const raw = localStorage.getItem(workItemsStorageKey(userId));
    return raw === null ? {} : parseMarks(JSON.parse(raw));
  } catch {
    return {};
  }
}

function writeMarks(userId: string, marks: WorkItemMarks): void {
  try {
    localStorage.setItem(workItemsStorageKey(userId), JSON.stringify(marks));
  } catch {
    // The item still updates in memory; a failed preference write is not a
    // reason to fabricate or hide a work item.
  }
}

/**
 * Per-user work-item read/dismiss state, mirroring the prototype's
 * `dc.v3.notifications.<identity>` local store. No server write is involved.
 */
export function useWorkItemMarks(userId: string | undefined) {
  const [marks, setMarks] = useState<WorkItemMarks>(() =>
    userId ? readMarks(userId) : {},
  );

  useEffect(() => {
    setMarks(userId ? readMarks(userId) : {});
  }, [userId]);

  const update = useCallback(
    (id: string, patch: Partial<WorkItemMark>) => {
      if (!userId || !id) return;
      setMarks((current) => {
        const previous = current[id] ?? { read: false, dismissed: false };
        const next = { ...current, [id]: { ...previous, ...patch } };
        writeMarks(userId, next);
        return next;
      });
    },
    [userId],
  );

  return {
    marks,
    markRead: (id: string) => update(id, { read: true }),
    dismiss: (id: string) => update(id, { read: true, dismissed: true }),
  };
}
