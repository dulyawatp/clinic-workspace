/**
 * Read model for the clinician/nurse surfaces added in task 5.
 *
 * These surfaces have no verified backend contract, so every row here comes
 * from the Demo/Sample fixture layer (`fixtures/data`, `data-ops`,
 * `data-clinical`) and is rendered behind an explicit sample label. Nothing in
 * this module performs a read or a write; a later native contract replaces the
 * row source, not the components.
 *
 * Two authored sources describe the same external-lab concept: the prototype's
 * `DC.data.externalLabs` rows (ordered/sent/received/reviewed) and the
 * `data-clinical` gap rows (collected/cancelled/private file). They share the
 * installed `External Lab Request` vocabulary, so they are normalised into one
 * view model here instead of being rendered as two unrelated lists.
 */
import type { BadgeTone } from "../../design/components";
import { coreData, type L10n, type Visit } from "../../fixtures/data";
import {
  type LabFile,
  labSamples,
  nurseAssignments,
  procedureConsumableBatches,
  procedureTemplateConsumables,
  resolveProcedureConsumables,
  upcomingProcedures,
} from "../../fixtures/data-clinical";
import {
  type Batch,
  type ExternalLabStatus,
  opsData,
} from "../../fixtures/data-ops";

/**
 * The fixture account carries no practitioner link (`SessionDTO` has no such
 * field and the demo accounts are role-only). The designed "my day" needs a
 * clinician, so the surfaces pin the authored sample persona and say so in the
 * UI instead of implying the signed-in user is that doctor.
 */
export const SAMPLE_DOCTOR_PRACTITIONER = "HP-01";
export const SAMPLE_DOCTOR_STAFF = "kitti";
export const SAMPLE_NURSE_STAFF = "maneerat";

export function sampleDoctorName(locale: "th" | "en"): string {
  return coreData.practitionerById(SAMPLE_DOCTOR_PRACTITIONER).name[locale];
}

export function sampleStaffName(id: string, locale: "th" | "en"): string {
  const staff = opsData.staff.find((row) => row.id === id);
  return staff ? staff.name[locale] : id;
}

export function patientLabel(patientId: string, locale: "th" | "en"): string {
  return coreData.patientName(patientId, locale);
}

export function practitionerLabel(
  practitionerId: string,
  locale: "th" | "en",
): string {
  return coreData.practitionerById(practitionerId).name[locale];
}

export function roomLabel(roomId: string, locale: "th" | "en"): string {
  return coreData.roomById(roomId).name[locale];
}

/** Presentation tone per designed status. Text always carries the state. */
export function labStatusTone(status: ExternalLabStatus): BadgeTone {
  switch (status) {
    case "ordered":
    case "sent":
      return "info";
    case "collected":
      return "warning";
    case "received":
    case "reviewed":
      return "success";
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

export function labStatusKey(status: ExternalLabStatus): string {
  return `lab.state.${status}`;
}

// ----------------------------------------------------------------- lab rows
export interface LabReportFile {
  readonly name: string;
  readonly uploadedBy: string | null;
  readonly uploadedAt: string | null;
  readonly sizeBytes: number | null;
  /** Sample extensions are private by design; storage stays unverified. */
  readonly isPrivate: boolean;
  readonly storageKey: string | null;
}

export interface LabRequestView {
  readonly id: string;
  readonly patientId: string;
  readonly visitId: string | null;
  readonly practitioner: string;
  readonly test: L10n;
  readonly provider: string;
  readonly status: ExternalLabStatus;
  /** `null` = the fixture author did not configure a priority (prototype). */
  readonly priority: "routine" | "urgent" | null;
  readonly category: "laboratory" | "pathology" | "other" | null;
  readonly orderedAt: string;
  readonly collectedAt: string | null;
  readonly collectedBy: string | null;
  readonly sentAt: string | null;
  readonly receivedAt: string | null;
  readonly receivedBy: string | null;
  readonly reviewedAt: string | null;
  readonly reviewedBy: string | null;
  readonly resultSummary: string | null;
  readonly interpretation: string | null;
  readonly externalRef: string | null;
  readonly sampleKind: string;
  readonly files: readonly LabReportFile[];
  /** A filename with no attachment is not evidence; keep it separate. */
  readonly fileNames: readonly string[];
  readonly cancelledAt: string | null;
  readonly cancellationReason: L10n | null;
  readonly modelSupport: "unverified";
  readonly sample: true;
}

function opsLabRows(): LabRequestView[] {
  return opsData.externalLabs.map((row) => ({
    id: row.id,
    patientId: row.patientId,
    visitId: row.visitId,
    practitioner: row.practitioner,
    test: row.test,
    provider: row.provider,
    status: row.status,
    priority: null,
    category: null,
    orderedAt: row.orderedAt,
    collectedAt: null,
    collectedBy: null,
    sentAt: row.sentAt,
    receivedAt: row.receivedAt,
    receivedBy: row.receivedBy,
    reviewedAt: row.reviewedAt,
    reviewedBy: row.reviewedBy,
    resultSummary: null,
    interpretation: row.interpretation ?? null,
    externalRef: row.externalRef,
    sampleKind: row.sampleKind,
    files: [],
    fileNames: row.file ? [row.file] : [],
    cancelledAt: null,
    cancellationReason: null,
    modelSupport: "unverified",
    sample: true,
  }));
}

function authoredLabFiles(files: readonly LabFile[]): LabReportFile[] {
  return files.map((file) => ({
    name: file.name,
    uploadedBy: file.uploadedBy,
    uploadedAt: file.uploadedAt,
    sizeBytes: file.sizeBytes,
    isPrivate: file.isPrivate,
    storageKey: file.storageKey,
  }));
}

function authoredLabRows(): LabRequestView[] {
  return labSamples.map((row) => ({
    id: row.id,
    patientId: row.patientId,
    visitId: row.visitId,
    practitioner: row.practitioner,
    test: row.test,
    provider: row.provider,
    status: row.status,
    priority: row.priority,
    category: row.category,
    orderedAt: row.orderedAt,
    collectedAt: row.collectedAt,
    collectedBy: row.collectedBy,
    sentAt: row.sentAt,
    receivedAt: row.receivedAt,
    receivedBy: null,
    reviewedAt: row.reviewedAt,
    reviewedBy: row.reviewedBy,
    resultSummary: row.resultSummary,
    interpretation: row.interpretation,
    externalRef: row.externalRef,
    sampleKind: row.sampleKind,
    files: authoredLabFiles(row.files),
    fileNames: [],
    cancelledAt: row.cancelledAt,
    cancellationReason: row.cancellationReason,
    modelSupport: "unverified",
    sample: true,
  }));
}

/** Newest request first; ties keep the fixture author's order. */
export const labRequests: readonly LabRequestView[] = [
  ...opsLabRows(),
  ...authoredLabRows(),
].sort((a, b) => (a.orderedAt < b.orderedAt ? 1 : -1));

export const LAB_STATUSES: readonly ExternalLabStatus[] = [
  "ordered",
  "collected",
  "sent",
  "received",
  "reviewed",
  "cancelled",
];

export function labRequestById(id: string): LabRequestView | null {
  return labRequests.find((row) => row.id === id) ?? null;
}

export function labRequestsForPatient(
  patientId: string,
): readonly LabRequestView[] {
  return labRequests.filter((row) => row.patientId === patientId);
}

/** Prototype `labContextValid`: send/receive need a linked, matching visit. */
export function labLinkVerified(row: LabRequestView): boolean {
  if (!row.visitId) return false;
  const visit = coreData.visitById(row.visitId);
  return visit !== null && visit.patientId === row.patientId;
}

// -------------------------------------------------------- procedure rows
export type ProcedureStatus = "in-progress" | "completed" | "scheduled";

export interface ProcedureConsumableLine {
  readonly lineIndex: number;
  readonly itemId: string;
  readonly itemName: L10n | null;
  readonly qty: number;
  readonly uom: string;
  readonly batch: Batch;
}

export interface ProcedureView {
  readonly id: string;
  readonly patientId: string;
  readonly encounterId: string | null;
  readonly appointmentId: string | null;
  readonly templateId: string;
  readonly status: ProcedureStatus;
  readonly at: string;
  readonly room: string;
  readonly consumables: readonly ProcedureConsumableLine[];
}

function consumableLine(
  templateId: string,
  lineIndex: number,
  batch: Batch,
): ProcedureConsumableLine | null {
  const template = procedureTemplateConsumables.find(
    (line) => line.templateId === templateId && line.lineIndex === lineIndex,
  );
  if (!template) return null;
  return {
    lineIndex,
    itemId: template.itemId,
    itemName: opsData.itemById(template.itemId)?.name ?? null,
    qty: template.qty,
    uom: template.uom,
    batch,
  };
}

function consumablesFor(
  procedureId: string,
  encounterId: string | null,
  templateId: string,
): ProcedureConsumableLine[] {
  // Precomputed rows win: they are the authored resolution for this procedure.
  const stored = procedureConsumableBatches.filter(
    (row) => row.procedureId === procedureId,
  );
  const rows =
    stored.length > 0
      ? stored
      : resolveProcedureConsumables({ procedureId, encounterId, templateId });
  return rows
    .map((row) => {
      const batch = opsData.batchById(row.batchId);
      return batch === null
        ? null
        : consumableLine(row.templateId, row.lineIndex, batch);
    })
    .filter((line): line is ProcedureConsumableLine => line !== null);
}

function visitProcedureRows(): ProcedureView[] {
  const rows: ProcedureView[] = [];
  for (const visit of coreData.visits) {
    for (const line of visit.procedures ?? []) {
      // The resolved batch row is the only source for the procedure template,
      // so a procedure without one is left out instead of guessing a template.
      const resolved = procedureConsumableBatches.find(
        (row) => row.procedureId === line.id,
      );
      if (!resolved) continue;
      const template = opsData.procedureTemplates.find(
        (row) => row.id === resolved.templateId,
      );
      rows.push({
        id: line.id,
        patientId: visit.patientId,
        encounterId: visit.id,
        appointmentId: visit.appointmentId,
        templateId: resolved.templateId,
        status: line.status,
        at: line.at,
        room: template?.room ?? "",
        consumables: consumablesFor(line.id, visit.id, resolved.templateId),
      });
    }
  }
  return rows;
}

function scheduledProcedureRows(): ProcedureView[] {
  return upcomingProcedures.map((row) => ({
    id: row.id,
    patientId: row.patientId,
    encounterId: null,
    appointmentId: row.appointmentId,
    templateId: row.templateId,
    status: "scheduled" as const,
    at: row.scheduledAt,
    room: row.room,
    consumables: consumablesFor(row.id, null, row.templateId),
  }));
}

export const procedures: readonly ProcedureView[] = [
  ...visitProcedureRows(),
  ...scheduledProcedureRows(),
];

export function procedureById(id: string): ProcedureView | null {
  return procedures.find((row) => row.id === id) ?? null;
}

export function proceduresForPatient(
  patientId: string,
): readonly ProcedureView[] {
  return procedures.filter((row) => row.patientId === patientId);
}

export function templateName(templateId: string, locale: "th" | "en"): string {
  const template = opsData.procedureTemplates.find(
    (row) => row.id === templateId,
  );
  return template ? template.name[locale] : templateId;
}

// ------------------------------------------------------------- nurse rows
export interface NurseCohortRow {
  readonly assignmentId: string;
  readonly patientId: string;
  readonly appointmentId: string;
  readonly practitionerId: string;
  readonly vitalsRecordedAt: string | null;
  readonly prepComplete: boolean;
  readonly sample: true;
}

export const nurseCohort: readonly NurseCohortRow[] = nurseAssignments.map(
  (row) => ({
    assignmentId: row.id,
    patientId: row.patientId,
    appointmentId: row.appointmentId,
    practitionerId: row.practitioner,
    vitalsRecordedAt: row.vitals?.measuredAt ?? null,
    prepComplete:
      row.prepChecklist.identity &&
      row.prepChecklist.consent &&
      row.prepChecklist.room &&
      row.prepChecklist.supplies,
    sample: true as const,
  }),
);

export function nurseAssignmentFor(
  patientId: string,
  appointmentId?: string | null,
) {
  return (
    nurseAssignments.find(
      (row) =>
        row.patientId === patientId &&
        (!appointmentId || row.appointmentId === appointmentId),
    ) ?? null
  );
}

export function nurseCohortDefault(): NurseCohortRow | null {
  return nurseCohort[0] ?? null;
}

/** Patient/visit coherence check used by both nursing surfaces. */
export function nurseContextValid(
  patientId: string,
  appointmentId: string | null,
): boolean {
  if (coreData.patientById(patientId) === null) return false;
  if (!appointmentId) return true;
  const appointment = coreData.appointmentById(appointmentId);
  return appointment !== null && appointment.patientId === patientId;
}

export function draftVisitsFor(practitionerId: string): Visit[] {
  return coreData.visits.filter(
    (visit) => visit.practitioner === practitionerId && visit.state === "draft",
  );
}

export function receivedLabsFor(
  practitionerId: string,
): readonly LabRequestView[] {
  return labRequests.filter(
    (row) => row.practitioner === practitionerId && row.status === "received",
  );
}

export function followUpVisitsFor(practitionerId: string): Visit[] {
  return coreData.visits.filter(
    (visit) => visit.practitioner === practitionerId && visit.note.followup,
  );
}
