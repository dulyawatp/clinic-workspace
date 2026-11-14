/**
 * Build-time opt-in for the optional native (Frappe) read transport.
 *
 * `VITE_CLINIC_NATIVE=1` at dev-server start or build time switches the read
 * surface to the Frappe methods; any other value (including unset) keeps the
 * existing proxy transport. Vite inlines `import.meta.env` at build time, so
 * this is a constant per build, never a runtime toggle.
 *
 * The native build does not rely on a shell variable: `pnpm build:native`
 * (`vite build --mode native`) defines `import.meta.env.VITE_CLINIC_NATIVE` as
 * `"1"` in `app/vite.native.ts`, so the Frappe-hosted bundle cannot be produced
 * with the proxy transport. Every other mode leaves the flag unset.
 */
export function nativeTransportEnabled(): boolean {
  return import.meta.env.VITE_CLINIC_NATIVE === "1";
}
