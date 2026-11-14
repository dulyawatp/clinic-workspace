import type { VisitSection } from "../../domain/clinical";

export const SECTION_ORDER: VisitSection[] = [
  "overview",
  "history",
  "exam",
  "note",
  "diagnoses",
  "medications",
  "procedures",
  "lab",
  "photos",
  "review",
];

export function sectionLabelKey(section: VisitSection): string {
  return `clinical.section.${section}`;
}
