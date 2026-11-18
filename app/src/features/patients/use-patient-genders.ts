import { useEffect, useState } from "react";
import { useSession } from "../../app/SessionProvider";
import { nativeTransportEnabled } from "../../transport/native/flag";
import { fetchNativePatientGenders } from "../../transport/native/patient-genders";

/**
 * The fixture/proxy sex values. They stay in place outside the native build and
 * whenever the native write is not available, so the sample form's behaviour is
 * unchanged.
 */
export const LEGACY_GENDER_OPTIONS: readonly string[] = [
  "Male",
  "Female",
  "Other",
  "Unknown",
];

export type PatientGenderOptions =
  | { readonly state: "loading" }
  | { readonly state: "error" }
  | { readonly state: "ready"; readonly items: readonly string[] };

const LOADING: PatientGenderOptions = { state: "loading" };
const ERROR: PatientGenderOptions = { state: "error" };
const LEGACY: PatientGenderOptions = {
  state: "ready",
  items: LEGACY_GENDER_OPTIONS,
};

const LABEL_KEYS: Readonly<Record<string, string>> = {
  Male: "patient.sexMale",
  Female: "patient.sexFemale",
  Other: "patient.sexOther",
  Unknown: "patient.sexUnknown",
};

/** Catalogue key for a known value; unknown server values render verbatim. */
export function patientGenderLabelKey(value: string): string | undefined {
  return LABEL_KEYS[value];
}

/**
 * Sex values a native patient form may offer.
 *
 * Outside the native build, or when the native write is not in play, this is
 * the unchanged fixture/proxy list and nothing is fetched. In the native build
 * it reads the exact server values once the session is live, retrying on every
 * open and on a new session identity. A failed or empty read stays `error` so
 * the caller can refuse to submit rather than fall back to invented values.
 */
export function usePatientGenderOptions(enabled: boolean): PatientGenderOptions {
  const { status, session, authGeneration } = useSession();
  const nativeBuild = nativeTransportEnabled();
  const nativeLive =
    enabled && nativeBuild && status === "authenticated" && session?.mode === "live";
  const [options, setOptions] = useState<PatientGenderOptions>(LOADING);

  useEffect(() => {
    if (!nativeLive) return;
    const controller = new AbortController();
    setOptions(LOADING);
    fetchNativePatientGenders(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setOptions(items.length === 0 ? ERROR : { state: "ready", items });
      })
      .catch(() => {
        if (!controller.signal.aborted) setOptions(ERROR);
      });
    return () => controller.abort();
  }, [nativeLive, authGeneration]);

  if (!enabled || !nativeBuild) return LEGACY;
  if (!nativeLive) return LOADING;
  return options;
}
