import { createContext, type ReactNode, useContext } from "react";
import {
  type Capability,
  type CapabilityMap,
  getCapability,
} from "../../domain/capability";

const CapabilityContext = createContext<CapabilityMap>({});

export function CapabilityProvider({
  value,
  children,
}: {
  value: CapabilityMap;
  children: ReactNode;
}) {
  return (
    <CapabilityContext.Provider value={value}>
      {children}
    </CapabilityContext.Provider>
  );
}

export function useCapabilities() {
  const caps = useContext(CapabilityContext);
  return {
    caps,
    get: (capability: Capability) => getCapability(caps, capability),
  };
}
