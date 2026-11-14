import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CapabilityMap } from "../domain/capability";
import { CapabilityProvider } from "../features/shell/CapabilityProvider";
import { LocaleProvider } from "../i18n/LocaleProvider";
import { SessionProvider, useSession } from "./SessionProvider";

const EMPTY_CAPABILITIES: CapabilityMap = {};

function AppErrorFallback() {
  const { t } = useTranslation();
  return (
    <div role="alert" data-testid="error-boundary">
      {t("errors.UNKNOWN")}
    </div>
  );
}

class AppErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override render() {
    return this.state.failed ? <AppErrorFallback /> : this.props.children;
  }
}

/**
 * Capabilities come from the session payload only. Missing entries stay
 * default-deny (`read_only`) through `getCapability`, never inferred from roles.
 */
function SessionCapabilities({ children }: { children: ReactNode }) {
  const { session } = useSession();
  return (
    <CapabilityProvider value={session?.capabilities ?? EMPTY_CAPABILITIES}>
      {children}
    </CapabilityProvider>
  );
}

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <LocaleProvider initial="th">
        <AppErrorBoundary>
          <SessionProvider>
            <SessionCapabilities>{children}</SessionCapabilities>
          </SessionProvider>
        </AppErrorBoundary>
      </LocaleProvider>
    </QueryClientProvider>
  );
}
