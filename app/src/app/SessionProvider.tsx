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
import { ROLE_HOME, surfacePath } from "../design/surfaces";
import type { RoleCode } from "../domain/capability";
import { type AppError, fromWire } from "../domain/errors";
import { isRoleList, isSessionDTO, type SessionDTO } from "../domain/session";
import { postLogout } from "../transport/auth";
import { nativeTransportEnabled } from "../transport/native/flag";
import { setClinicForbiddenHandler, setClinicRequestContext } from "../transport/native/frappe";
import { assertLegacyRoute } from "../transport/native/legacy";
import {
  clearNativeCsrfToken,
  fetchNativeSession,
  inspectNativeSession,
  subscribeNativeAuthInvalidation,
} from "../transport/native/session";

export type SessionStatus = "loading" | "authenticated" | "workspace" | "anonymous" | "error";
type NativeHoursAuth = "confirmed" | "checking" | "unavailable";

export interface SessionRefreshResult {
  status: CheckedSessionState["status"] | "superseded";
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
  /** Resume verification gates doctor hours without unmounting the workspace. */
  nativeHoursAuth: NativeHoursAuth;
  refresh: () => Promise<SessionRefreshResult>;
  openWorkspaceChooser: () => Promise<void>;
  chooseWorkspace: (role: RoleCode) => Promise<void>;
  workspaceHomePending: string | null;
  clearWorkspaceHomePending: () => void;
  workspaceSelectionFailed: boolean;
  previousWorkspace: RoleCode | undefined;
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
  workspaceSelectionFailed?: boolean;
  previousWorkspace?: RoleCode;
}

type CheckedSessionState = SessionState & {
  status: Exclude<SessionStatus, "loading">;
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

const PREFERENCE_KEY = "clinic.workspace";

function readPreference(epoch: string): RoleCode | undefined {
  try {
    const value = sessionStorage.getItem(PREFERENCE_KEY);
    const roles = [value?.startsWith(`${epoch}:`) ? value.slice(epoch.length + 1) : undefined];
    return isRoleList(roles) ? roles[0] : undefined;
  } catch {
    return undefined;
  }
}

function savePreference(epoch: string, workspace: RoleCode): void {
  try {
    sessionStorage.setItem(PREFERENCE_KEY, `${epoch}:${workspace}`);
  } catch {
    // Storage may be disabled; the confirmed workspace still works in memory.
  }
}

function sameNativeSession(first: SessionDTO, next: SessionDTO): boolean {
  return first.sessionEpoch === next.sessionEpoch && first.user.id === next.user.id;
}

/** Only the negotiated native workspace flow shares unresolved write markers. */
export function nativeWriteMarkerOwner(session: SessionDTO | null): string | undefined {
  return session?.mode === "live" && session.eligibleWorkspaces
    ? JSON.stringify([session.sessionEpoch, session.user.id])
    : undefined;
}

function workspaceChooser(
  session: SessionDTO,
  previousWorkspace?: RoleCode,
  workspaceSelectionFailed = false,
): CheckedSessionState {
  return {
    status: "workspace",
    session: {
      mode: "live",
      sessionEpoch: session.sessionEpoch,
      user: { ...session.user, roles: [] },
      capabilities: {},
      eligibleWorkspaces: session.eligibleWorkspaces ?? session.user.roles,
    },
    previousWorkspace,
    workspaceSelectionFailed,
  };
}

async function fetchSession(
  preferred: { epoch: string; role: RoleCode } | undefined,
  isCurrent: () => boolean,
): Promise<CheckedSessionState> {
  if (nativeTransportEnabled()) {
    const session = await fetchNativeSession();
    if (!isCurrent()) return CHECK_ERROR;
    if (!session) return ANONYMOUS;
    const role = preferred?.epoch === session.sessionEpoch
      ? preferred.role
      : readPreference(session.sessionEpoch);
    if (role && session.user.roles[0] !== role) {
      const choice = session.eligibleWorkspaces?.find((entry) => entry === role);
      if (!choice) return workspaceChooser(session, undefined, true);
      try {
        const selected = await fetchNativeSession(choice);
        if (!selected) return ANONYMOUS;
        if (!sameNativeSession(session, selected)) return CHECK_ERROR;
        return { status: "authenticated", session: selected };
      } catch {
        // Selection might have been revoked between the two reads. Only use
        // fresh choices, never fall back to a different active workspace.
        if (!isCurrent()) return CHECK_ERROR;
        const latest = await fetchNativeSession();
        if (!latest) return ANONYMOUS;
        if (!sameNativeSession(session, latest)) return CHECK_ERROR;
        return workspaceChooser(latest, undefined, true);
      }
    }
    return session.user.roles.length === 0
      ? workspaceChooser(session)
      : { status: "authenticated", session };
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
  const [hoursAuth, setHoursAuth] = useState<{
    generation: number;
    status: NativeHoursAuth;
  }>();
  const stateRef = useRef<SessionState>(LOADING);
  const preference = useRef<{ epoch: string; role: RoleCode } | undefined>(undefined);
  const [workspaceHomePending, setWorkspaceHomePending] = useState<string | null>(null);
  // Last confirmed server session survives loading/error/anonymous snapshots.
  // Request/cache generations change on every check, not only replacements.
  const confirmedEpoch = useRef<string | null>(null);
  const ambiguousHoursEpoch = useRef<string | null>(null);
  const markerOwner = useRef<string | undefined>(undefined);
  const lifetime = useRef(0);
  const [logoutFailure, setLogoutFailure] = useState<AppError | null>(null);

  const commit = useCallback(
    (state: SessionState) => {
      stateRef.current = state;
      const nextIdentity = identityOf(state);
      if ((state.status === "authenticated" || state.status === "workspace") && state.session) {
        if (ambiguousHoursEpoch.current !== state.session.sessionEpoch)
          ambiguousHoursEpoch.current = null;
        confirmedEpoch.current = state.session.sessionEpoch;
        markerOwner.current = nativeTransportEnabled() ? nativeWriteMarkerOwner(state.session) : undefined;
      }
      if (nativeTransportEnabled() && (state.status !== "authenticated" || nextIdentity !== identity.current)) {
        const role = state.status === "authenticated" && state.session?.user.roles[0];
        setClinicRequestContext(role && state.session ? {
          workspace: role,
          sessionEpoch: state.session.sessionEpoch,
        } : null);
      }
      if (state.status === "anonymous") {
        markerOwner.current = undefined;
        setWorkspaceHomePending(null);
        preference.current = undefined;
        if (nativeTransportEnabled()) {
          try { sessionStorage.removeItem(PREFERENCE_KEY); } catch { /* optional storage */ }
        }
      }
      if (nextIdentity !== identity.current) {
        identity.current = nextIdentity;
        authGeneration.current += 1;
        // Logout feedback belongs to the identity that attempted it, never to
        // a replacement one.
        setLogoutFailure(null);
        if (queryClient) {
          void queryClient.cancelQueries();
          const owner = markerOwner.current;
          const keepMarker = (query: { meta?: Record<string, unknown> }) =>
            owner !== undefined && query.meta?.nativeWriteMarker === owner;
          queryClient.removeQueries({ predicate: (query) => !keepMarker(query) });
          for (const query of queryClient.getQueryCache().findAll({ predicate: keepMarker })) {
            const reset = query.meta?.resetNativeWorkspaceState;
            if (typeof reset === "function") reset();
          }
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

  const clearWorkspaceHomePending = useCallback(() => {
    setWorkspaceHomePending(null);
  }, []);

  const refresh = useCallback(async (): Promise<SessionRefreshResult> => {
    const current = generation.current + 1;
    generation.current = current;

    // Unverified identity must expose neither protected content nor cached rows.
    commit(LOADING);
    const isCurrent = () => generation.current === current;

    let next: CheckedSessionState;
    try {
      next = await fetchSession(preference.current, isCurrent);
    } catch {
      next = CHECK_ERROR;
    }

    if (generation.current !== current) {
      return { status: "superseded", isCurrent };
    }

    commit(next);
    return { status: next.status, isCurrent };
  }, [commit]);

  const openWorkspaceChooser = useCallback(async () => {
    const before = stateRef.current;
    const previous = before.session;
    if (before.status !== "authenticated" || !previous || (previous.eligibleWorkspaces?.length ?? 0) < 2) return;
    const current = ++generation.current;
    commit(LOADING);
    let next: CheckedSessionState;
    try {
      const session = await fetchNativeSession();
      next = !session ? ANONYMOUS
        : sameNativeSession(previous, session)
          ? workspaceChooser(session, previous.user.roles[0])
          : CHECK_ERROR;
    } catch {
      next = CHECK_ERROR;
    }
    if (generation.current === current) commit(next);
  }, [commit]);

  const chooseWorkspace = useCallback(async (role: RoleCode) => {
    const before = stateRef.current;
    const previous = before.session;
    if (before.status !== "workspace" || !previous?.eligibleWorkspaces?.includes(role)) return;
    const current = ++generation.current;
    commit(LOADING);
    let next: CheckedSessionState;
    try {
      const session = await fetchNativeSession(role);
      if (generation.current !== current) return;
      if (!session) {
        next = ANONYMOUS;
      } else if (!sameNativeSession(previous, session)) {
        next = CHECK_ERROR;
      } else {
        savePreference(session.sessionEpoch, role);
        preference.current = { epoch: session.sessionEpoch, role };
        setWorkspaceHomePending(surfacePath(ROLE_HOME[role]));
        next = { status: "authenticated", session };
      }
    } catch {
      if (generation.current !== current) return;
      try {
        const latest = await fetchNativeSession();
        next = !latest ? ANONYMOUS
          : sameNativeSession(previous, latest)
            ? workspaceChooser(latest, before.previousWorkspace, true)
            : CHECK_ERROR;
      } catch {
        next = CHECK_ERROR;
      }
    }
    if (generation.current === current) commit(next);
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
    if (!nativeTransportEnabled()) return;
    let pending: { generation: number; checksHours: boolean } | undefined;
    const reportHoursAuth = (status: NativeHoursAuth) => {
      setHoursAuth({ generation: authGeneration.current, status });
    };
    const recheckWorkspace = (onResume: boolean) => {
      const before = stateRef.current;
      const session = before.session;
      const role = session?.user.roles[0];
      if (before.status !== "authenticated" || !session || !role) return;
      // One-role native doctors need the same hours check on resume. Other
      // workspaces keep the existing negotiated-workspace revocation check.
      const checksHours = onResume && role === "DOC";
      if (!checksHours && !session.eligibleWorkspaces) return;
      const current = generation.current;
      if (pending?.generation === current && (!checksHours || pending.checksHours)) return;
      const check = { generation: current, checksHours };
      pending = check;
      const isCurrent = () => generation.current === current && pending === check;
      if (checksHours) reportHoursAuth("checking");
      void inspectNativeSession().then(
        (latest) => {
          if (!isCurrent()) return;
          if (!latest || !sameNativeSession(session, latest)) {
            generation.current += 1;
            commit(latest ? CHECK_ERROR : ANONYMOUS);
            return;
          }
          const eligible = latest.eligibleWorkspaces ?? latest.user.roles;
          if (!eligible.includes(role)) {
            if (!session.eligibleWorkspaces) {
              generation.current += 1;
              commit(CHECK_ERROR);
              return;
            }
            // Keep the old preference until the user makes a fresh choice.
            preference.current = { epoch: session.sessionEpoch, role };
            savePreference(session.sessionEpoch, role);
            generation.current += 1;
            commit(workspaceChooser(latest, role, true));
          } else {
            if (session.eligibleWorkspaces && eligible.join("|") !== session.eligibleWorkspaces.join("|")) {
              // The active role is still assigned. Update the menu without
              // swapping context, unmounting forms, or changing capabilities.
              commit({ status: "authenticated", session: { ...session, eligibleWorkspaces: eligible } });
            }
            if (checksHours) reportHoursAuth("confirmed");
          }
        },
        (cause: unknown) => {
          if (!isCurrent()) return;
          if (checksHours) reportHoursAuth("unavailable");
          if (session.eligibleWorkspaces && safeError(cause).code === "FORBIDDEN") {
            generation.current += 1;
            commit(CHECK_ERROR);
          }
        },
      ).finally(() => {
        if (pending === check) pending = undefined;
      });
    };
    const onFocus = () => {
      if (document.visibilityState === "visible") recheckWorkspace(true);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (stateRef.current.session?.user.roles[0] === "DOC") recheckWorkspace(true);
      } else if (stateRef.current.session?.user.roles[0] === "DOC") {
        // A check that started before hiding cannot confirm the next resume.
        if (pending?.checksHours) pending = undefined;
        reportHoursAuth("checking");
      }
    };
    const unsubscribe = subscribeNativeAuthInvalidation((ambiguous) => {
      if (ambiguous) ambiguousHoursEpoch.current = confirmedEpoch.current;
      clearNativeCsrfToken();
      void refresh();
    });
    setClinicForbiddenHandler(() => recheckWorkspace(false));
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      unsubscribe();
      pending = undefined;
      setClinicForbiddenHandler(undefined);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [commit, refresh]);

  useEffect(() => {
    void refresh();
    return () => {
      generation.current += 1;
      lifetime.current += 1;
      if (nativeTransportEnabled()) setClinicRequestContext(undefined);
    };
  }, [refresh]);

  const value = useMemo<SessionContextValue>(
    () => ({
      status: snapshot.state.status,
      session: snapshot.state.session,
      authGeneration: snapshot.authGeneration,
      nativeHoursAuth: snapshot.state.session?.sessionEpoch === ambiguousHoursEpoch.current && ambiguousHoursEpoch.current !== null
        ? "unavailable"
        : hoursAuth?.generation === snapshot.authGeneration ? hoursAuth.status : "confirmed",
      refresh,
      openWorkspaceChooser,
      chooseWorkspace,
      workspaceHomePending,
      clearWorkspaceHomePending,
      workspaceSelectionFailed: snapshot.state.workspaceSelectionFailed ?? false,
      previousWorkspace: snapshot.state.previousWorkspace,
      logout,
      logoutFailure,
      clearLogoutFailure,
    }),
    [snapshot, hoursAuth, refresh, openWorkspaceChooser, chooseWorkspace, workspaceHomePending,
      clearWorkspaceHomePending, logout, logoutFailure, clearLogoutFailure],
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
