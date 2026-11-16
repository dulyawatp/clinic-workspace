import { useCallback, useEffect, useState } from "react";
import {
  procedureCreateLocked,
  procedureWriteLocked,
  subscribeProcedureWriteLock,
} from "../../transport/native/procedure-write";

/**
 * Browser-visible procedure write lock. The value is read from storage on every
 * mount, so a reload after an uncertain completion or acknowledgement shows the
 * stop state again. There is no automatic unlock.
 */
export function useProcedureWriteLock(): {
  locked: boolean;
  refreshLock: () => void;
} {
  const [locked, setLocked] = useState(procedureWriteLocked);

  const refreshLock = useCallback(() => {
    setLocked(procedureWriteLocked());
  }, []);

  useEffect(() => {
    refreshLock();
    const unsubscribe = subscribeProcedureWriteLock(refreshLock);
    window.addEventListener("storage", refreshLock);
    return () => {
      unsubscribe();
      window.removeEventListener("storage", refreshLock);
    };
  }, [refreshLock]);

  return { locked, refreshLock };
}

/**
 * Browser-visible creation lock. Same rules as the completion lock: read on
 * every mount, never unlocked automatically.
 */
export function useProcedureCreateLock(): {
  locked: boolean;
  refreshLock: () => void;
} {
  const [locked, setLocked] = useState(procedureCreateLocked);

  const refreshLock = useCallback(() => {
    setLocked(procedureCreateLocked());
  }, []);

  useEffect(() => {
    refreshLock();
    const unsubscribe = subscribeProcedureWriteLock(refreshLock);
    window.addEventListener("storage", refreshLock);
    return () => {
      unsubscribe();
      window.removeEventListener("storage", refreshLock);
    };
  }, [refreshLock]);

  return { locked, refreshLock };
}
