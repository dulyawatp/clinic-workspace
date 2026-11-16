import { useCallback, useEffect, useState } from "react";
import {
  appointmentWriteLocked,
  subscribeAppointmentWriteLock,
} from "../../transport/native/appointment-write";

/**
 * Browser-visible appointment write lock. The value is read from storage on
 * every mount, so a reload after an uncertain save or acknowledgement shows the
 * stop state again. There is no automatic unlock.
 */
export function useAppointmentWriteLock(): {
  locked: boolean;
  refreshLock: () => void;
} {
  const [locked, setLocked] = useState(appointmentWriteLocked);

  const refreshLock = useCallback(() => {
    setLocked(appointmentWriteLocked());
  }, []);

  useEffect(() => {
    refreshLock();
    const unsubscribe = subscribeAppointmentWriteLock(refreshLock);
    window.addEventListener("storage", refreshLock);
    return () => {
      unsubscribe();
      window.removeEventListener("storage", refreshLock);
    };
  }, [refreshLock]);

  return { locked, refreshLock };
}
