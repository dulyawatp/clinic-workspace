import { QueryClientContext } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type AppError, fromWire } from "../domain/errors";
import { isSessionDTO, type SessionDTO } from "../domain/session";
import { postLogout } from "../transport/auth";
import { nativeTransportEnabled } from "../transport/native/flag";
import { assertLegacyRoute } from "../transport/native/legacy";
import { fetchNativeSession } from "../transport/native/session";

export type SessionStatus = "loading" | "authenticated" | "anonymous" | "error";

export interface SessionRefreshResult {
  status: "authenticated" | "anonymous" | "error" | "superseded";
  /** Check again immediately before caller side effects; newer checks invalidate it. */
  isCurrent: () => boolean;
}

export interface SessionContextValue {
  status: SessionStatus;
  session: SessionDTO | null;
  /**
   * Monotonic identity generation. It changes on every session identity
   * transition (anonymous/loading, different session/user/roles), so read
   * queries can key their cache per identity and never observe a previous
   * user's rows.
   */
  authGeneration: number;
  refresh: () => Promise<SessionRefreshResult>;
  /** Owns successful logout reconciliation independently of initiating page lifetime. */
  logout: () => Promise<SessionRefreshResult>;
  /**
   * Safe failure of the last logout attempt, kept only for the identity that
   * attempted it. Revocation unmounts the initiating page, so the provider
   * retains this until that surface shows it once.
   */
  logoutFailure: AppError | null;
  /** One-shot consumption: feedback never survives into a later view. */
  clearLogoutFailure: () => void;
}

interface SessionState {
  status: SessionStatus;
  session: SessionDTO | null;
}

type CheckedSessionState = SessionState & {
  status: "authenticated" | "anonymous" | "error";
};

interface SessionSnapshot {
  state: SessionState;
  authGeneration: number;
}

/** A missing or expired session is a normal anonymous state, never an error. */
const ANONYMOUS: CheckedSessionState = { status: "anonymous", session: null };
const LOADING: SessionState = { status: "loading", session: null };
const CHECK_ERROR: CheckedSessionState = { status: "error", session: null };

const SessionContext = createContext<SessionContextValue | null>(null);

async function fetchSession(): Promise<CheckedSessionState> {
  if (nativeTransportEnabled()) {
    try {
      const session = await fetchNativeSession();
      // No session upstream is a normal anonymous state, exactly like 401 here.
      return session === null
        ? ANONYMOUS
        : { status: "authenticated", session };
    } catch {
      return CHECK_ERROR;
    }
  }

  assertLegacyRoute();
  const response = await fetch("/api/session", {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });

  // No retry path for 401: a rejected session is simply anonymous.
  if (response.status === 401) return ANONYMOUS;
  if (!response.ok) return CHECK_ERROR;

  const body: unknown = await response.json();
  return isSessionDTO(body)
    ? { status: "authenticated", session: body }
    : CHECK_ERROR;
}

/**
 * Identity of a session state. Roles are part of it because the same user
 * account could come back with a different role set, which changes the
 * fixture read surface.
 */
function identityOf(state: SessionState): string {
  if (state.status !== "authenticated" || state.session === null) {
    return state.status;
  }
  return [
    state.status,
    state.session.sessionEpoch,
    state.session.mode ?? "fixture",
    state.session.user.id,
    state.session.user.roles.join("|"),
  ].join(":");
}

/**
 * Safe client error for a failed logout request: only the machine code leaves
 * the transport, never upstream text.
 */
function safeError(cause: unknown): AppError {
  const code =
    typeof cause === "object" && cause !== null && "code" in cause
      ? fromWire(cause.code)
      : "UNKNOWN";
  const retryable =
    typeof cause === "object" &&
    cause !== null &&
    "retryable" in cause &&
    cause.retryable === true;
  return { code, retryable };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useContext(QueryClientContext);
  const [snapshot, setSnapshot] = useState<SessionSnapshot>({
    state: LOADING,
    authGeneration: 0,
  });
  // Monotonic request generation: a stalled earlier response never overwrites a
  // newer one or a state update after unmount.
  const generation = useRef(0);
  const identity = useRef(identityOf(LOADING));
  const authGeneration = useRef(0);
  // Last confirmed server session survives loading/error/anonymous snapshots.
  // Request/cache generations change on every check, not only replacements.
  const confirmedEpoch = useRef<string | null>(null);
  const lifetime = useRef(0);
  const [logoutFailure, setLogoutFailure] = useState<AppError | null>(null);

  const commit = useCallback(
    (state: SessionState) => {
      if (state.status === "authenticated" && state.session) {
        confirmedEpoch.current = state.session.sessionEpoch;
      }
      const nextIdentity = identityOf(state);
      if (nextIdentity !== identity.current) {
        identity.current = nextIdentity;
        authGeneration.current += 1;
        // Logout feedback belongs to the identity that attempted it, never to
        // a replacement one.
        setLogoutFailure(null);
        if (queryClient) {
          void queryClient.cancelQueries();
          queryClient.removeQueries();
        }
      }
      setSnapshot({ state, authGeneration: authGeneration.current });
    },
    [queryClient],
  );

  /**
   * Drops the unverified local identity and every cached row immediately, and
   * supersedes any in-flight session check. Used before a logout request is
   * sent, so no protected content outlives the decision to sign out.
   */
  const revoke = useCallback(() => {
    generation.current += 1;
    commit(LOADING);
  }, [commit]);

  const clearLogoutFailure = useCallback(() => {
    setLogoutFailure(null);
  }, []);

  const refresh = useCallback(async (): Promise<SessionRefreshResult> => {
    const current = generation.current + 1;
    generation.current = current;

    // Unverified identity must expose neither protected content nor cached rows.
    commit(LOADING);
    const isCurrent = () => generation.current === current;

    let next: CheckedSessionState;
    try {
      next = await fetchSession();
    } catch {
      next = CHECK_ERROR;
    }

    if (generation.current !== current) {
      return { status: "superseded", isCurrent };
    }

    commit(next);
    return { status: next.status, isCurrent };
  }, [commit]);

  const logout = useCallback(async (): Promise<SessionRefreshResult> => {
    const originatingEpoch = confirmedEpoch.current;
    const originatingIdentity = identity.current;
    const originatingLifetime = lifetime.current;
    // A new attempt replaces any previous feedback: exactly one is surfaced.
    setLogoutFailure(null);
    // Revoke before the request: protected UI and cache are gone whatever the
    // outcome turns out to be, and only a confirmed server response restores
    // them.
    revoke();

    let failure: AppError | null = null;
    try {
      await postLogout();
    } catch (cause) {
      failure = safeError(cause);
    }

    if (lifetime.current !== originatingLifetime) {
      return { status: "superseded", isCurrent: () => false };
    }
    // A session confirmed after the revoke belongs to a newer login and
    // supersedes this logout. Same-session checks (loading/error included)
    // must still reconcile.
    if (
      confirmedEpoch.current !== null &&
      confirmedEpoch.current !== originatingEpoch
    ) {
      return { status: "superseded", isCurrent: () => false };
    }

    // Reconcile with a GET on success and on failure; the POST is never
    // repeated. A failed reconciliation leaves the error state in place.
    const result = await refresh();
    if (failure !== null && result.status === "authenticated") {
      // The reconciliation GET can itself confirm a replacement identity that
      // landed before it. Feedback must never cross identities, so epoch,
      // full identity and currency are rechecked after the await, not only
      // before it.
      if (
        !result.isCurrent() ||
        confirmedEpoch.current !== originatingEpoch ||
        identity.current !== originatingIdentity
      ) {
        return { status: "superseded", isCurrent: () => false };
      }
      // Revocation unmounted the initiating route (the real router guards the
      // whole shell), so only provider state can still report this failure to
      // the same identity. It is retained here and consumed once by the
      // profile surface, never shown to a replacement identity.
      setLogoutFailure(failure);
      throw failure;
    }
    return result;
  }, [refresh, revoke]);

  useEffect(() => {
    void refresh();
    return () => {
      generation.current += 1;
      lifetime.current += 1;
    };
  }, [refresh]);

  const value = useMemo<SessionContextValue>(
    () => ({
      status: snapshot.state.status,
      session: snapshot.state.session,
      authGeneration: snapshot.authGeneration,
      refresh,
      logout,
      logoutFailure,
      clearLogoutFailure,
    }),
    [snapshot, refresh, logout, logoutFailure, clearLogoutFailure],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession must be used within a SessionProvider");
  }
  return value;
}
