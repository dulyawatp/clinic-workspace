/**
 * Clinician- and patient-authored text is never translated.
 *
 * Identity by contract: callers must route free text through this function
 * instead of `t()` so that a future translation layer cannot silently rewrite
 * clinical notes, diagnosis text, or identifiers used as display text.
 */
export function translateClinicalText(text: string): string {
  return text;
}
