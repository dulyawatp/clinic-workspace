/**
 * Demo/Sample fixtures — part 3: authored clinical gaps.
 *
 * The prototype left these designed components without records:
 *  - `DC.data.photos = []` (no frames, annotations or versions),
 *  - consent beyond `{photo, at, version}` (no signer/evidence/consent date,
 *    withdrawal, guardian, expiry, form record or redaction note),
 *  - patient identity/national ID and persisted emergency contacts,
 *  - lab `collected`/`cancelled` states, private files, category/priority/
 *    result summary,
 *  - nurse duties/upcoming procedures/assignment source, prep/aftercare,
 *  - DOC-01 recent encounters, DOC-02 amendment + native-lab sample,
 *  - DOC-03 per-consumable lines and template-driven batches.
 *
 * Shapes follow the installed backend vocabulary so a later native swap is a
 * data replacement (task-2 brief): photo rows use the `clinic_photos` DocType
 * fields (`patient_reference`, `original_file`, `storage_key`,
 * `original_sha256`, `original_width`, `original_height`; annotation versions
 * use `version_number`, `annotation_file`, `storage_key`, `state`; drafts use
 * `current_version_number`/`current_version`; annotation version `state` uses
 * the installed Select values `pending`/`committed` and links its Draft),
 * consent rows use `Clinic Patient Consent` semantics (status Active|Revoked,
 * required signature file + signed datetime), and encounter amendments model a
 * Frappe amendment (`amended_from` + reason). Everything is `sample: true`.
 *
 * Backend field mapping for the authored clinical rows (TS → DocType field):
 *   consent  → Clinic Patient Consent: patient, consent_type, status
 *              (Active|Revoked), signature_file, signed_datetime; extended
 *              fixture-only fields (signer_relation, guardian evidence_type,
 *              evidence_ref, valid_upto, withdrawn_at) are sample extensions
 *   identity → Patient: national_id (custom), blood_group, occupation,
 *              preferred_language, photo_access_restricted; Patient Relation
 *              child rows: name, relation, phone, is_primary, consent_to_contact
 *   labs     → External Lab Request (extension): patient, encounter, provider,
 *              test_template, status, priority, report_category,
 *              result_summary, external_ref, cancelled_at/cancel_reason;
 *              private files map to File.private = 1 (server storage unverified)
 *   nursing  → Nursing Task (extension): patient, appointment, practitioner,
 *              assigned_by, prep_identity/prep_consent/prep_room/prep_supplies,
 *              aftercare_instructions, followup_date, acknowledged
 *   DOC-02   → Patient Encounter Amendment (extension): amended_from, reason,
 *              amendment_number, changes[section, field, before, after]
 *   DOC-03   → Clinical Procedure Template Item: parent, item_code, qty, uom
 */

import photoAssetsJson from "@clinic/contracts/fixtures/photo-assets.json";
import type { L10n, Visit } from "./data";
import { clinicDates, coreData, patientById } from "./data";
import type { Batch, ProcedureTemplate } from "./data-ops";
import {
  businessDay,
  daysToExpiry,
  opsData,
  procedureTemplates,
} from "./data-ops";

export const SAMPLE_LABEL =
  "Demo/Sample clinical data — fictional rows authored for designed-but-unwired surfaces; not a Marley backend contract";

const dates = clinicDates(new Date(coreData.now));

interface PhotoAsset {
  readonly fileId: string;
  readonly photoId: string;
  readonly patientId: string;
  readonly bodyArea: string;
  readonly viewpoint: string;
  readonly category: string;
  readonly fileName: string;
  readonly contentType: string;
  readonly bytes: number;
  readonly sha256: string;
  readonly width: number;
  readonly height: number;
  readonly dataBase64: string;
}

const photoAssets = (photoAssetsJson as { assets: PhotoAsset[] }).assets;

function assetAt(index: number): PhotoAsset {
  const asset = photoAssets[index];
  if (!asset) throw new Error(`missing synthetic photo asset #${index}`);
  return asset;
}

export interface PhotoFrame {
  readonly id: string;
  readonly docType: "Clinic Photo";
  readonly patientId: string;
  readonly patientReference: string;
  readonly category: "before" | "after";
  readonly bodyArea: string;
  readonly bodyAreaLabel: L10n;
  readonly viewpoint: string;
  readonly capturedAt: string;
  readonly originalFile: string;
  readonly storageKey: string;
  readonly originalSha256: string;
  readonly width: number;
  readonly height: number;
  readonly url: string;
  readonly consentStatus: "recorded" | "missing";
  readonly sample: true;
}

export type AnnotationShape =
  | {
      readonly kind: "rect";
      readonly x: number;
      readonly y: number;
      readonly width: number;
      readonly height: number;
      readonly stroke: string;
    }
  | {
      readonly kind: "ellipse";
      readonly x: number;
      readonly y: number;
      readonly radiusX: number;
      readonly radiusY: number;
      readonly stroke: string;
    }
  | {
      readonly kind: "arrow";
      readonly from: Point;
      readonly to: Point;
      readonly stroke: string;
    }
  | {
      readonly kind: "freehand";
      readonly points: readonly Point[];
      readonly strokeWidth: number;
      readonly stroke: string;
    };

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface AnnotationVersion {
  readonly id: string;
  readonly docType: "Clinic Photo Annotation Version";
  /** Installed required Link → Clinic Photo Draft. */
  readonly draft: string;
  readonly photoId: string;
  readonly versionNumber: number;
  readonly annotationFile: string;
  readonly storageKey: string;
  /** Installed Select options: `pending` | `committed`. */
  readonly state: "pending" | "committed";
  readonly shapes: readonly AnnotationShape[];
  readonly createdAt: string;
  readonly author: string;
  readonly sample: true;
}

export interface PhotoDraft {
  readonly id: string;
  readonly docType: "Clinic Photo Draft";
  readonly photoId: string;
  readonly author: string;
  readonly currentVersionNumber: number;
  readonly currentVersionId: string;
  readonly updatedAt: string;
  readonly sample: true;
}

const beforeAsset = assetAt(0);
const afterAsset = assetAt(1);

export const photoFrames: PhotoFrame[] = [
  {
    id: "SAMPLE-PH-3001",
    docType: "Clinic Photo",
    patientId: "P-10241",
    patientReference: "P-10241",
    category: "before",
    bodyArea: beforeAsset.bodyArea,
    bodyAreaLabel: { th: "ศีรษะ (ด้านหน้า)", en: "Scalp (frontal)" },
    viewpoint: beforeAsset.viewpoint,
    capturedAt: dates.at(-28, "10:20"),
    originalFile: beforeAsset.fileName,
    storageKey: `demo/clinic-photo/${beforeAsset.fileId}`,
    originalSha256: beforeAsset.sha256,
    width: beforeAsset.width,
    height: beforeAsset.height,
    url: `/api/files/${beforeAsset.fileId}`,
    consentStatus: "recorded",
    sample: true,
  },
  {
    id: "SAMPLE-PH-3002",
    docType: "Clinic Photo",
    patientId: "P-10241",
    patientReference: "P-10241",
    category: "after",
    bodyArea: afterAsset.bodyArea,
    bodyAreaLabel: { th: "ศีรษะ (ด้านหน้า)", en: "Scalp (frontal)" },
    viewpoint: afterAsset.viewpoint,
    capturedAt: dates.at(-1, "10:05"),
    originalFile: afterAsset.fileName,
    storageKey: `demo/clinic-photo/${afterAsset.fileId}`,
    originalSha256: afterAsset.sha256,
    width: afterAsset.width,
    height: afterAsset.height,
    url: `/api/files/${afterAsset.fileId}`,
    consentStatus: "recorded",
    sample: true,
  },
];

// Normalized (0..1) coordinates so the client annotation engine can scale
// without touching the original pixels.
export const annotationVersions: AnnotationVersion[] = [
  {
    id: "SAMPLE-ANV-3001-1",
    docType: "Clinic Photo Annotation Version",
    draft: "SAMPLE-PHD-3001",
    photoId: "SAMPLE-PH-3001",
    versionNumber: 1,
    annotationFile: "annotations/SAMPLE-PH-3001-v1.json",
    storageKey: "demo/clinic-photo/annotations/SAMPLE-PH-3001-v1.json",
    state: "committed",
    shapes: [
      {
        kind: "rect",
        x: 0.32,
        y: 0.18,
        width: 0.36,
        height: 0.4,
        stroke: "#0f766e",
      },
      {
        kind: "arrow",
        from: { x: 0.7, y: 0.12 },
        to: { x: 0.58, y: 0.3 },
        stroke: "#0f766e",
      },
    ],
    createdAt: dates.at(-28, "10:32"),
    author: "kitti",
    sample: true,
  },
  {
    id: "SAMPLE-ANV-3001-2",
    docType: "Clinic Photo Annotation Version",
    draft: "SAMPLE-PHD-3001",
    photoId: "SAMPLE-PH-3001",
    versionNumber: 2,
    annotationFile: "annotations/SAMPLE-PH-3001-v2.json",
    storageKey: "demo/clinic-photo/annotations/SAMPLE-PH-3001-v2.json",
    state: "pending",
    shapes: [
      {
        kind: "rect",
        x: 0.3,
        y: 0.16,
        width: 0.4,
        height: 0.44,
        stroke: "#0f766e",
      },
      {
        kind: "ellipse",
        x: 0.5,
        y: 0.38,
        radiusX: 0.14,
        radiusY: 0.1,
        stroke: "#155e75",
      },
      {
        kind: "freehand",
        points: [
          { x: 0.34, y: 0.2 },
          { x: 0.44, y: 0.28 },
          { x: 0.52, y: 0.33 },
        ],
        strokeWidth: 0.01,
        stroke: "#155e75",
      },
      {
        kind: "arrow",
        from: { x: 0.74, y: 0.1 },
        to: { x: 0.6, y: 0.26 },
        stroke: "#0f766e",
      },
    ],
    createdAt: dates.at(0, "09:15"),
    author: "kitti",
    sample: true,
  },
  {
    id: "SAMPLE-ANV-3002-1",
    docType: "Clinic Photo Annotation Version",
    draft: "SAMPLE-PHD-3002",
    photoId: "SAMPLE-PH-3002",
    versionNumber: 1,
    annotationFile: "annotations/SAMPLE-PH-3002-v1.json",
    storageKey: "demo/clinic-photo/annotations/SAMPLE-PH-3002-v1.json",
    state: "committed",
    shapes: [
      {
        kind: "ellipse",
        x: 0.5,
        y: 0.36,
        radiusX: 0.16,
        radiusY: 0.12,
        stroke: "#0f766e",
      },
    ],
    createdAt: dates.at(-1, "10:18"),
    author: "kitti",
    sample: true,
  },
];

export const photoDrafts: PhotoDraft[] = [
  {
    id: "SAMPLE-PHD-3001",
    docType: "Clinic Photo Draft",
    photoId: "SAMPLE-PH-3001",
    author: "kitti",
    currentVersionNumber: 2,
    currentVersionId: "SAMPLE-ANV-3001-2",
    updatedAt: dates.at(0, "09:15"),
    sample: true,
  },
  {
    id: "SAMPLE-PHD-3002",
    docType: "Clinic Photo Draft",
    photoId: "SAMPLE-PH-3002",
    author: "kitti",
    currentVersionNumber: 1,
    currentVersionId: "SAMPLE-ANV-3002-1",
    updatedAt: dates.at(-1, "10:18"),
    sample: true,
  },
];

// ------------------------------------------------------------------ consent
export interface ConsentFormRecord {
  readonly id: string;
  readonly docType: "Patient Consent Form (sample extension)";
  readonly version: string;
  readonly title: L10n;
  readonly scope: "photo";
  readonly effectiveFrom: string;
  readonly retentionYears: number;
  readonly languages: readonly ["th", "en"];
  readonly sample: true;
}

export const consentForm: ConsentFormRecord = {
  id: "PC-FORM-2.1",
  docType: "Patient Consent Form (sample extension)",
  version: "PC-2.1",
  title: {
    th: "หนังสือยินยอมให้เก็บและใช้ภาพถ่ายทางการแพทย์",
    en: "Photo capture and use consent",
  },
  scope: "photo",
  effectiveFrom: dates.at(-365, "00:00"),
  retentionYears: 10,
  languages: ["th", "en"],
  sample: true,
};

export type ConsentRecordStatus = "Active" | "Revoked";

/**
 * Installed `Clinic Patient Consent` semantics: status is Active|Revoked and a
 * signature file + signed datetime are required. Fields the installed model
 * does not guarantee are marked `(sample extension)`; display-level words such
 * as "Granted"/"Withdrawn"/"Expired" exist only as labels.
 */
export interface ConsentRecord {
  readonly id: string;
  readonly docType: "Clinic Patient Consent";
  readonly patientId: string;
  readonly consentType: "photo";
  readonly status: ConsentRecordStatus;
  /** Display label only; "Expired" is derived from `validUntil`. */
  readonly stateLabel: L10n;
  /** Required by the installed model. */
  readonly signatureFile: string;
  /** Required by the installed model. */
  readonly signedDatetime: string;
  readonly signerName: string;
  readonly signerRelation: "self" | "guardian";
  readonly consentDate: string;
  readonly version: string;
  readonly capturedBy: string;
  readonly consentFormId: string;
  /** (sample extension) guardian flag beyond Active|Revoked. */
  readonly signerIsGuardian: boolean;
  /** (sample extension) evidence classification. */
  readonly evidenceType: "signed-form" | "digital" | "witnessed-verbal";
  /** (sample extension) evidence reference. */
  readonly evidenceRef: string;
  /** (sample extension) consent validity horizon. */
  readonly validUntil: string | null;
  /** (sample extension) revocation timestamp. */
  readonly withdrawnAt: string | null;
  /** (sample extension) revocation reason. */
  readonly withdrawnReason: string | null;
  readonly sample: true;
}

/** Extended consent fields that are fixture-only, not installed requirements. */
export const CONSENT_EXTENSION_FIELDS = [
  "signerIsGuardian",
  "evidenceType",
  "evidenceRef",
  "validUntil",
  "withdrawnAt",
  "withdrawnReason",
] as const;

const TODAY = dates.todayKey();

function consentStateLabel(
  status: ConsentRecordStatus,
  validUntil: string | null,
): L10n {
  if (status === "Revoked") return { th: "เพิกถอนแล้ว", en: "Withdrawn" };
  const until = validUntil ? businessDay(validUntil) : null;
  if (until && until < TODAY) return { th: "หมดอายุ", en: "Expired" };
  return { th: "ให้ความยินยอม", en: "Granted" };
}

const consentRows: Omit<ConsentRecord, "stateLabel">[] = [
  {
    id: "SAMPLE-CONSENT-1041",
    docType: "Clinic Patient Consent",
    patientId: "P-10241",
    consentType: "photo",
    status: "Active",
    signatureFile: "demo/consent/SAMPLE-CONSENT-1041.pdf",
    signedDatetime: dates.at(-95, "10:05"),
    signerName: "ณัฐธิดา ศรีสวัสดิ์",
    signerRelation: "self",
    consentDate: dates.at(-95, "10:04"),
    version: "PC-2.1",
    capturedBy: "araya",
    consentFormId: "PC-FORM-2.1",
    signerIsGuardian: false,
    evidenceType: "signed-form",
    evidenceRef: "PC-FORM-2.1/1041",
    validUntil: dates.at(270, "23:59"),
    withdrawnAt: null,
    withdrawnReason: null,
    sample: true,
  },
  {
    id: "SAMPLE-CONSENT-1043",
    docType: "Clinic Patient Consent",
    patientId: "P-10243",
    consentType: "photo",
    status: "Revoked",
    signatureFile: "demo/consent/SAMPLE-CONSENT-1043.pdf",
    signedDatetime: dates.at(-400, "11:22"),
    signerName: "ธนวัฒน์ รุ่งเรือง",
    signerRelation: "self",
    consentDate: dates.at(-400, "11:20"),
    version: "PC-2.0",
    capturedBy: "araya",
    consentFormId: "PC-FORM-2.1",
    signerIsGuardian: false,
    evidenceType: "signed-form",
    evidenceRef: "PC-FORM-2.0/1043",
    validUntil: null,
    withdrawnAt: dates.at(-30, "14:10"),
    withdrawnReason: "ผู้ป่วยขอยกเลิกการใช้ภาพถ่ายเพื่อการสอน",
    sample: true,
  },
  {
    id: "SAMPLE-CONSENT-1044",
    docType: "Clinic Patient Consent",
    patientId: "P-10244",
    consentType: "photo",
    status: "Active",
    signatureFile: "demo/consent/SAMPLE-CONSENT-1044.pdf",
    signedDatetime: dates.at(-380, "14:15"),
    signerName: "สุดารัตน์ อินทรีย์",
    signerRelation: "self",
    consentDate: dates.at(-380, "14:12"),
    version: "PC-2.1",
    capturedBy: "araya",
    consentFormId: "PC-FORM-2.1",
    signerIsGuardian: false,
    evidenceType: "digital",
    evidenceRef: "PC-FORM-2.1/1044",
    validUntil: dates.at(-2, "23:59"),
    withdrawnAt: null,
    withdrawnReason: null,
    sample: true,
  },
  {
    id: "SAMPLE-CONSENT-1048",
    docType: "Clinic Patient Consent",
    patientId: "P-10248",
    consentType: "photo",
    status: "Active",
    signatureFile: "demo/consent/SAMPLE-CONSENT-1048.pdf",
    signedDatetime: dates.at(-13, "08:57"),
    signerName: "เบญจวรรณ จันทร์เพ็ญ",
    signerRelation: "self",
    consentDate: dates.at(-13, "08:55"),
    version: "PC-2.1",
    capturedBy: "maneerat",
    consentFormId: "PC-FORM-2.1",
    signerIsGuardian: false,
    evidenceType: "digital",
    evidenceRef: "PC-FORM-2.1/1048",
    validUntil: dates.at(352, "23:59"),
    withdrawnAt: null,
    withdrawnReason: null,
    sample: true,
  },
  {
    // Guardian consent sample: the minor is an authored fixture only, not a
    // directory record (the ported patient set stays at 180 records).
    id: "SAMPLE-CONSENT-MINOR-01",
    docType: "Clinic Patient Consent",
    patientId: "SAMPLE-P-2001",
    consentType: "photo",
    status: "Active",
    signatureFile: "demo/consent/SAMPLE-CONSENT-MINOR-01.pdf",
    signedDatetime: dates.at(-40, "09:32"),
    signerName: "สุพัตรา ใจดี",
    signerRelation: "guardian",
    consentDate: dates.at(-40, "09:30"),
    version: "PC-2.1",
    capturedBy: "araya",
    consentFormId: "PC-FORM-2.1",
    signerIsGuardian: true,
    evidenceType: "signed-form",
    evidenceRef: "PC-FORM-2.1/GUARDIAN-01",
    validUntil: dates.at(325, "23:59"),
    withdrawnAt: null,
    withdrawnReason: null,
    sample: true,
  },
];

export const consentRecords: ConsentRecord[] = consentRows.map((row) => ({
  ...row,
  stateLabel: consentStateLabel(row.status, row.validUntil),
}));

/** Per-role clinical redaction notice (PAT-03 designed block). */
export interface RedactionNotice {
  readonly id: string;
  readonly docType: "Role Redaction Notice (sample extension)";
  readonly role: "REC" | "NUR" | "PHA" | "CAS" | "MGR" | "ADM" | "DOC";
  readonly seesClinicalDetail: boolean;
  readonly redactedSections: readonly string[];
  readonly note: L10n;
  readonly sample: true;
}

export const clinicalRedactionNotices: RedactionNotice[] = [
  {
    id: "SAMPLE-REDACT-REC",
    docType: "Role Redaction Notice (sample extension)",
    role: "REC",
    seesClinicalDetail: false,
    redactedSections: ["diagnoses", "note", "labs"],
    note: {
      th: "ผู้รับลงทะเบียนเห็นเฉพาะข้อมูลการนัดและธุรการ",
      en: "Reception sees appointment and administrative data only",
    },
    sample: true,
  },
  {
    id: "SAMPLE-REDACT-NUR",
    docType: "Role Redaction Notice (sample extension)",
    role: "NUR",
    seesClinicalDetail: false,
    redactedSections: ["diagnoses", "note.assessment"],
    note: {
      th: "พยาบาลเห็นงานที่ได้รับมอบหมาย ไม่เห็นการวินิจฉัย",
      en: "Nursing sees assigned tasks, not diagnosis detail",
    },
    sample: true,
  },
  {
    id: "SAMPLE-REDACT-DOC",
    docType: "Role Redaction Notice (sample extension)",
    role: "DOC",
    seesClinicalDetail: true,
    redactedSections: [],
    note: {
      th: "แพทย์เจ้าของการตรวจเห็นข้อมูลทางคลินิกทั้งหมด",
      en: "Treating doctor sees the full clinical record",
    },
    sample: true,
  },
];

// --------------------------------------------------------- patient identity
export interface EmergencyContact {
  readonly id: string;
  readonly docType: "Patient Relation";
  readonly name: string;
  readonly relation: string;
  readonly phone: string;
  readonly isPrimary: boolean;
  readonly consentToContact: boolean;
  readonly updatedAt: string;
}

export interface PatientIdentityProfile {
  readonly patientId: string;
  readonly docType: "Patient";
  /** Non-dialable placeholder national ID: never a real citizen number. */
  readonly nationalId: string;
  readonly nationalIdType: "thai-id" | "passport";
  readonly passportNo: string | null;
  readonly bloodGroup: string | null;
  readonly occupation: L10n | null;
  readonly address: L10n;
  readonly preferredLanguage: "th" | "en";
  readonly photoAccessRestricted: boolean;
  readonly emergencyContacts: readonly EmergencyContact[];
  readonly updatedAt: string;
  readonly sample: true;
}

export const patientProfiles: PatientIdentityProfile[] = [
  {
    patientId: "P-10241",
    docType: "Patient",
    nationalId: "0-0000-00000-00-0",
    nationalIdType: "thai-id",
    passportNo: null,
    bloodGroup: "B",
    occupation: { th: "นักออกแบบกราฟิก", en: "Graphic designer" },
    address: { th: "เขตวัฒนา กรุงเทพมหานคร", en: "Watthana, Bangkok" },
    preferredLanguage: "th",
    photoAccessRestricted: true,
    emergencyContacts: [
      {
        id: "SAMPLE-REL-1041-1",
        docType: "Patient Relation",
        name: "ปวีณา ศรีสวัสดิ์",
        relation: "sibling",
        phone: "08-FIXTURE-1041",
        isPrimary: true,
        consentToContact: true,
        updatedAt: dates.at(-95, "10:10"),
      },
    ],
    updatedAt: dates.at(-95, "10:10"),
    sample: true,
  },
  {
    patientId: "P-10243",
    docType: "Patient",
    nationalId: "0-0000-00000-00-0",
    nationalIdType: "thai-id",
    passportNo: null,
    bloodGroup: "O",
    occupation: { th: "วิศวกร", en: "Engineer" },
    address: { th: "เขตบางรัก กรุงเทพมหานคร", en: "Bang Rak, Bangkok" },
    preferredLanguage: "th",
    photoAccessRestricted: false,
    emergencyContacts: [
      {
        id: "SAMPLE-REL-1043-1",
        docType: "Patient Relation",
        name: "กมลชนก รุ่งเรือง",
        relation: "spouse",
        phone: "08-FIXTURE-1043",
        isPrimary: true,
        consentToContact: true,
        updatedAt: dates.at(-30, "14:15"),
      },
    ],
    updatedAt: dates.at(-30, "14:15"),
    sample: true,
  },
  {
    patientId: "P-10244",
    docType: "Patient",
    nationalId: "0-0000-00000-00-0",
    nationalIdType: "thai-id",
    passportNo: null,
    bloodGroup: "AB",
    occupation: null,
    address: { th: "เขตจตุจักร กรุงเทพมหานคร", en: "Chatuchak, Bangkok" },
    preferredLanguage: "en",
    photoAccessRestricted: false,
    emergencyContacts: [
      {
        id: "SAMPLE-REL-1044-1",
        docType: "Patient Relation",
        name: "อนุชา อินทรีย์",
        relation: "parent",
        phone: "08-FIXTURE-1044",
        isPrimary: true,
        consentToContact: false,
        updatedAt: dates.at(-380, "14:20"),
      },
    ],
    updatedAt: dates.at(-380, "14:20"),
    sample: true,
  },
  {
    // Minor used only for the guardian-consent sample above.
    patientId: "SAMPLE-P-2001",
    docType: "Patient",
    nationalId: "0-0000-00000-00-0",
    nationalIdType: "thai-id",
    passportNo: null,
    bloodGroup: null,
    occupation: null,
    address: { th: "เขตพระนคร กรุงเทพมหานคร", en: "Phra Nakhon, Bangkok" },
    preferredLanguage: "th",
    photoAccessRestricted: false,
    emergencyContacts: [
      {
        id: "SAMPLE-REL-2001-1",
        docType: "Patient Relation",
        name: "สุพัตรา ใจดี",
        relation: "guardian",
        phone: "08-FIXTURE-2001",
        isPrimary: true,
        consentToContact: true,
        updatedAt: dates.at(-40, "09:35"),
      },
    ],
    updatedAt: dates.at(-40, "09:35"),
    sample: true,
  },
];

// --------------------------------------------------------------------- labs
export type LabCategory = "laboratory" | "pathology" | "other";
export type LabPriority = "routine" | "urgent";

export interface LabFile {
  readonly name: string;
  readonly isPrivate: true;
  readonly storageKey: string;
  readonly uploadedBy: string;
  readonly uploadedAt: string;
  readonly sizeBytes: number;
}

export interface LabSample {
  readonly id: string;
  readonly docType: "External Lab Request (sample extension)";
  readonly patientId: string;
  readonly visitId: string | null;
  readonly practitioner: string;
  readonly test: L10n;
  readonly testId: string;
  readonly providerId: string;
  readonly provider: string;
  readonly status: "collected" | "cancelled" | "received";
  readonly priority: LabPriority;
  readonly category: LabCategory;
  readonly orderedAt: string;
  readonly collectedAt: string | null;
  readonly collectedBy: string | null;
  readonly sentAt: string | null;
  readonly receivedAt: string | null;
  readonly reviewedAt: string | null;
  readonly reviewedBy: string | null;
  readonly resultSummary: string | null;
  readonly interpretation: string | null;
  readonly externalRef: string | null;
  readonly sampleKind: string;
  readonly files: readonly LabFile[];
  readonly cancelledAt: string | null;
  /** Paired sample copy so the reason stays readable in TH and EN. */
  readonly cancellationReason: L10n | null;
  /** Installed-site support for collected/cancelled/private files is unproven. */
  readonly modelSupport: "unverified";
  readonly sample: true;
}

export const labSamples: LabSample[] = [
  {
    id: "SAMPLE-LAB-79",
    docType: "External Lab Request (sample extension)",
    patientId: "P-10245",
    visitId: null,
    practitioner: "HP-02",
    test: { th: "เฟอร์ริติน", en: "Ferritin" },
    testId: "LBT-02",
    providerId: "LP-01",
    provider: "Bangkok Central Lab",
    status: "collected",
    priority: "urgent",
    category: "laboratory",
    orderedAt: dates.at(0, "08:40"),
    collectedAt: dates.at(0, "09:05"),
    collectedBy: "maneerat",
    sentAt: null,
    receivedAt: null,
    reviewedAt: null,
    reviewedBy: null,
    resultSummary: null,
    interpretation: null,
    externalRef: null,
    sampleKind: "Blood 4 ml",
    files: [],
    cancelledAt: null,
    cancellationReason: null,
    modelSupport: "unverified",
    sample: true,
  },
  {
    id: "SAMPLE-LAB-80",
    docType: "External Lab Request (sample extension)",
    patientId: "P-10247",
    visitId: null,
    practitioner: "HP-03",
    test: { th: "ไทรอยด์ (TSH)", en: "Thyroid (TSH)" },
    testId: "LBT-03",
    providerId: "LP-02",
    provider: "Ramathibodi reference lab",
    status: "cancelled",
    priority: "routine",
    category: "laboratory",
    orderedAt: dates.at(-3, "11:10"),
    collectedAt: null,
    collectedBy: null,
    sentAt: null,
    receivedAt: null,
    reviewedAt: null,
    reviewedBy: null,
    resultSummary: null,
    interpretation: null,
    externalRef: null,
    sampleKind: "Blood 3 ml",
    files: [],
    cancelledAt: dates.at(-3, "13:00"),
    cancellationReason: {
      th: "ผู้ป่วยขอเลื่อนการตรวจ",
      en: "Patient asked to postpone the test",
    },
    modelSupport: "unverified",
    sample: true,
  },
  {
    id: "SAMPLE-LAB-78",
    docType: "External Lab Request (sample extension)",
    patientId: "P-10248",
    visitId: null,
    practitioner: "HP-02",
    test: { th: "ตรวจชิ้นเนื้อ (pathology)", en: "Skin biopsy (pathology)" },
    testId: "LBT-01",
    providerId: "LP-01",
    provider: "Bangkok Central Lab",
    status: "received",
    priority: "routine",
    category: "pathology",
    orderedAt: dates.at(-6, "10:00"),
    collectedAt: dates.at(-6, "10:30"),
    collectedBy: "maneerat",
    sentAt: dates.at(-6, "12:00"),
    receivedAt: dates.at(-2, "09:40"),
    reviewedAt: null,
    reviewedBy: null,
    resultSummary: "Benign inflammatory dermatosis; no malignancy seen.",
    interpretation: null,
    externalRef: "BCL-88302",
    sampleKind: "Tissue 4 mm",
    files: [
      {
        name: "pathology-78.pdf",
        isPrivate: true,
        storageKey: "demo/lab/SAMPLE-LAB-78/report.pdf",
        uploadedBy: "maneerat",
        uploadedAt: dates.at(-2, "09:42"),
        sizeBytes: 184_320,
      },
    ],
    cancelledAt: null,
    cancellationReason: null,
    modelSupport: "unverified",
    sample: true,
  },
];

// ------------------------------------------------------------------ nursing
export interface NurseDutyTile {
  readonly id: string;
  readonly label: L10n;
  readonly icon: string;
  readonly taskTab: "vitals" | "prep" | "aftercare" | "lab";
  readonly sample: true;
}

export const nurseDutyTiles: NurseDutyTile[] = [
  {
    id: "cohort",
    label: { th: "ผู้ป่วยตัวอย่างที่ดูแล", en: "Sample work queue" },
    icon: "users",
    taskTab: "vitals",
    sample: true,
  },
  {
    id: "vitals",
    label: { th: "สัญญาณชีพ", en: "Vitals" },
    icon: "pulse",
    taskTab: "vitals",
    sample: true,
  },
  {
    id: "prep",
    label: { th: "เตรียมหัตถการ", en: "Treatment prep" },
    icon: "clipboard",
    taskTab: "prep",
    sample: true,
  },
  {
    id: "aftercare",
    label: { th: "คำแนะนำหลังรักษา", en: "Aftercare" },
    icon: "check-circle",
    taskTab: "aftercare",
    sample: true,
  },
  {
    id: "lab",
    label: { th: "ส่งและรับผลแล็บ", en: "Lab handoff" },
    icon: "flask",
    taskTab: "lab",
    sample: true,
  },
  {
    id: "photos",
    label: { th: "ภาพถ่ายและความยินยอม", en: "Photos & consent" },
    icon: "camera",
    taskTab: "prep",
    sample: true,
  },
];

export interface NurseVitals {
  readonly temperature: number;
  readonly pulse: number;
  readonly systolic: number;
  readonly diastolic: number;
  readonly respiration: number;
  readonly height: number;
  readonly weight: number;
  readonly measuredAt: string;
}

export interface PrepChecklist {
  readonly identity: boolean;
  readonly consent: boolean;
  readonly room: boolean;
  readonly supplies: boolean;
  readonly note: string | null;
}

export interface AftercarePlan {
  readonly instructions: L10n | null;
  readonly followupDate: string | null;
  readonly acknowledged: boolean;
}

export interface NurseAssignment {
  readonly id: string;
  readonly docType: "Nursing Task (sample extension)";
  readonly patientId: string;
  readonly appointmentId: string;
  readonly practitioner: string;
  readonly nurse: string;
  /** Assignment is authored, not a server-verified assignment. */
  readonly assignment: {
    readonly source: "sample-authored";
    readonly assignedBy: string;
    readonly assignedAt: string;
    readonly verified: false;
  };
  readonly duties: readonly ("vitals" | "prep" | "aftercare" | "lab")[];
  readonly vitals: NurseVitals | null;
  readonly prepChecklist: PrepChecklist;
  readonly aftercare: AftercarePlan;
  readonly sample: true;
}

export const nurseAssignments: NurseAssignment[] = [
  {
    id: "SAMPLE-NUR-3301",
    docType: "Nursing Task (sample extension)",
    patientId: "P-10241",
    appointmentId: "DC-APT-3301",
    practitioner: "HP-01",
    nurse: "maneerat",
    assignment: {
      source: "sample-authored",
      assignedBy: "maneerat",
      assignedAt: dates.at(0, "08:20"),
      verified: false,
    },
    duties: ["vitals", "prep", "lab"],
    vitals: {
      temperature: 36.6,
      pulse: 78,
      systolic: 118,
      diastolic: 76,
      respiration: 16,
      height: 168,
      weight: 61,
      measuredAt: dates.at(0, "09:25"),
    },
    prepChecklist: {
      identity: true,
      consent: true,
      room: true,
      supplies: true,
      note: "ล็อตเลเซอร์และวันหมดอายุตรวจแล้ว",
    },
    aftercare: { instructions: null, followupDate: null, acknowledged: false },
    sample: true,
  },
  {
    id: "SAMPLE-NUR-3303",
    docType: "Nursing Task (sample extension)",
    patientId: "P-10243",
    appointmentId: "DC-APT-3303",
    practitioner: "HP-02",
    nurse: "maneerat",
    assignment: {
      source: "sample-authored",
      assignedBy: "maneerat",
      assignedAt: dates.at(0, "09:35"),
      verified: false,
    },
    duties: ["vitals", "prep"],
    vitals: null,
    prepChecklist: {
      identity: true,
      consent: false,
      room: false,
      supplies: false,
      note: null,
    },
    aftercare: { instructions: null, followupDate: null, acknowledged: false },
    sample: true,
  },
  {
    id: "SAMPLE-NUR-3306",
    docType: "Nursing Task (sample extension)",
    patientId: "P-10246",
    appointmentId: "DC-APT-3306",
    practitioner: "HP-01",
    nurse: "maneerat",
    assignment: {
      source: "sample-authored",
      assignedBy: "maneerat",
      assignedAt: dates.at(0, "11:15"),
      verified: false,
    },
    duties: ["vitals", "aftercare"],
    vitals: {
      temperature: 36.5,
      pulse: 72,
      systolic: 112,
      diastolic: 70,
      respiration: 15,
      height: 162,
      weight: 55,
      measuredAt: dates.at(0, "11:20"),
    },
    prepChecklist: {
      identity: true,
      consent: true,
      room: true,
      supplies: true,
      note: null,
    },
    aftercare: {
      instructions: {
        th: "หลีกเลี่ยงแสงแดด 2 สัปดาห์ ทาครีมกันแดดและติดตามอาการผื่นแดง",
        en: "Avoid sun for two weeks, use sunscreen and monitor redness",
      },
      followupDate: dates.at(28).slice(0, 10),
      acknowledged: true,
    },
    sample: true,
  },
];

export interface UpcomingProcedure {
  readonly id: string;
  readonly docType: "Clinical Procedure (sample)";
  readonly patientId: string;
  readonly appointmentId: string;
  readonly practitioner: string;
  readonly templateId: string;
  readonly scheduledAt: string;
  readonly room: string;
  readonly prepKeys: readonly string[];
  readonly sample: true;
}

export const upcomingProcedures: UpcomingProcedure[] = [
  {
    id: "SAMPLE-PROC-NEXT-01",
    docType: "Clinical Procedure (sample)",
    patientId: "P-10244",
    appointmentId: "DC-APT-3304",
    practitioner: "HP-03",
    templateId: "PRT-02",
    scheduledAt: dates.at(0, "10:30"),
    room: "SU-04",
    prepKeys: ["identity", "consent", "room", "supplies"],
    sample: true,
  },
  {
    id: "SAMPLE-PROC-NEXT-02",
    docType: "Clinical Procedure (sample)",
    patientId: "P-10245",
    appointmentId: "DC-APT-3305",
    practitioner: "HP-02",
    templateId: "PRT-04",
    scheduledAt: dates.at(0, "11:00"),
    room: "SU-03",
    prepKeys: ["identity", "consent", "room"],
    sample: true,
  },
];

// ------------------------------------------------------------ DOC-01/02/03
/** Recent encounters for one practitioner, newest first (DOC-01). */
export function recentEncountersFor(practitioner: string, limit = 5): Visit[] {
  return coreData.visits
    .filter((visit) => visit.practitioner === practitioner)
    .sort((a, b) => (a.date < b.date ? 1 : -1))
    .slice(0, limit);
}

export interface EncounterAmendment {
  readonly id: string;
  readonly docType: "Patient Encounter Amendment (sample extension)";
  readonly encounterId: string;
  readonly amendedFrom: string;
  readonly amendmentNumber: number;
  readonly reason: L10n;
  readonly amendedBy: string;
  readonly amendedAt: string;
  readonly status: "draft" | "signed";
  readonly changes: ReadonlyArray<{
    readonly section: string;
    readonly field: string;
    readonly before: string;
    readonly after: string;
  }>;
  readonly sample: true;
}

export const encounterAmendments: EncounterAmendment[] = [
  {
    id: "SAMPLE-AMEND-2042-1",
    docType: "Patient Encounter Amendment (sample extension)",
    encounterId: "DC-VIS-2042",
    amendedFrom: "DC-VIS-2042",
    amendmentNumber: 1,
    reason: {
      th: "เพิ่มความรุนแรงของสิวตามภาพถ่ายล่าสุด",
      en: "Severity updated from the latest photo review",
    },
    amendedBy: "kitti",
    amendedAt: dates.at(0, "18:10"),
    status: "draft",
    changes: [
      {
        section: "note",
        field: "assessment",
        before: "Acne vulgaris mild-moderate",
        after: "Acne vulgaris moderate; inflammatory lesions on both cheeks",
      },
    ],
    sample: true,
  },
];

export interface NativeLabSample {
  readonly id: string;
  readonly docType: "Lab Test";
  readonly kind: "native";
  readonly patientId: string;
  readonly encounterId: string;
  readonly practitioner: string;
  readonly templateId: string;
  readonly status: "ordered" | "result-ready";
  readonly modelSupport: "unverified";
  readonly wired: false;
  readonly note: L10n;
  readonly sample: true;
}

export const nativeLabSample: NativeLabSample = {
  id: "SAMPLE-LAB-NATIVE-01",
  docType: "Lab Test",
  kind: "native",
  patientId: "P-10241",
  encounterId: "DC-VIS-2041",
  practitioner: "HP-01",
  templateId: "LBT-01",
  status: "result-ready",
  modelSupport: "unverified",
  wired: false,
  note: {
    th: "แล็บภายในยังไม่ยืนยันโมเดลและสิทธิ์ — ตัวอย่างสำหรับเลย์เอาต์เท่านั้น",
    en: "Native lab model and permissions are unverified — layout sample only",
  },
  sample: true,
};

export interface ProcedureTemplateConsumable {
  readonly id: string;
  readonly docType: "Clinical Procedure Template Item";
  readonly templateId: string;
  readonly lineIndex: number;
  readonly itemId: string;
  readonly qty: number;
  readonly uom: string;
  readonly batchPolicy: "oldest-usable";
  readonly sample: true;
}

export const procedureTemplateConsumables: ProcedureTemplateConsumable[] =
  procedureTemplates.flatMap((template: ProcedureTemplate) =>
    template.consumables.map((itemId, lineIndex) => {
      const item = opsData.itemById(itemId);
      return {
        id: `SAMPLE-PTC-${template.id}-${lineIndex}`,
        docType: "Clinical Procedure Template Item" as const,
        templateId: template.id,
        lineIndex,
        itemId,
        qty: 1,
        uom: item?.uom ?? "pcs",
        batchPolicy: "oldest-usable" as const,
        sample: true as const,
      };
    }),
  );

export interface ProcedureConsumableBatch {
  readonly id: string;
  readonly docType: "Clinical Procedure Template Item";
  readonly procedureId: string;
  readonly encounterId: string | null;
  readonly templateId: string;
  readonly lineIndex: number;
  readonly itemId: string;
  readonly batchId: string;
  readonly batchLabel: string;
  readonly warehouse: string;
  readonly expiry: string;
  readonly qty: number;
  readonly sample: true;
}

function usableBatchFor(itemId: string): Batch | null {
  const usable = opsData.batches
    .filter((batch) => batch.itemId === itemId && batch.qty > 0)
    .filter((batch) => (daysToExpiry(batch.expiry) ?? -1) >= 0)
    .sort((a, b) => {
      if (a.warehouse !== b.warehouse) {
        return a.warehouse === "WH-ROOM"
          ? -1
          : b.warehouse === "WH-ROOM"
            ? 1
            : 0;
      }
      return a.expiry < b.expiry ? -1 : 1;
    });
  return usable[0] ?? null;
}

function consumableBatchRow(input: {
  procedureId: string;
  encounterId: string | null;
  templateId: string;
  lineIndex: number;
}): ProcedureConsumableBatch | null {
  const line = procedureTemplateConsumables.find(
    (row) =>
      row.templateId === input.templateId && row.lineIndex === input.lineIndex,
  );
  if (!line) return null;
  const batch = usableBatchFor(line.itemId);
  if (!batch) return null;
  return {
    id: `SAMPLE-PCB-${input.procedureId}-${input.lineIndex}`,
    docType: "Clinical Procedure Template Item",
    procedureId: input.procedureId,
    encounterId: input.encounterId,
    templateId: input.templateId,
    lineIndex: input.lineIndex,
    itemId: line.itemId,
    batchId: batch.id,
    batchLabel: batch.batchId,
    warehouse: batch.warehouse,
    expiry: batch.expiry,
    qty: line.qty,
    sample: true,
  };
}

/**
 * Batch resolution for the designed procedure screen. Replaces the prototype's
 * hard-coded lot `B-2411` (a lot no batch owns) with real, usable lots.
 */
export const procedureConsumableBatches: ProcedureConsumableBatch[] = [
  consumableBatchRow({
    procedureId: "DC-PROC-118",
    encounterId: "DC-VIS-2041",
    templateId: "PRT-01",
    lineIndex: 0,
  }),
  consumableBatchRow({
    procedureId: "SAMPLE-PROC-201",
    encounterId: null,
    templateId: "PRT-02",
    lineIndex: 0,
  }),
  consumableBatchRow({
    procedureId: "SAMPLE-PROC-201",
    encounterId: null,
    templateId: "PRT-02",
    lineIndex: 1,
  }),
].filter((row): row is ProcedureConsumableBatch => row !== null);

/**
 * Resolves a template to usable lots on demand, for surfaces that address a
 * procedure the precomputed rows above do not cover (task-5 DOC-03). A lot is
 * never invented: a consumable without usable stock is left out of the result.
 */
export function resolveProcedureConsumables(input: {
  procedureId: string;
  encounterId: string | null;
  templateId: string;
}): ProcedureConsumableBatch[] {
  return procedureTemplateConsumables
    .filter((line) => line.templateId === input.templateId)
    .map((line) =>
      consumableBatchRow({
        procedureId: input.procedureId,
        encounterId: input.encounterId,
        templateId: input.templateId,
        lineIndex: line.lineIndex,
      }),
    )
    .filter((row): row is ProcedureConsumableBatch => row !== null);
}

export const clinicalAuthored = {
  photoFrames,
  annotationVersions,
  photoDrafts,
  consentForm,
  consentRecords,
  clinicalRedactionNotices,
  patientProfiles,
  labSamples,
  nurseDutyTiles,
  nurseAssignments,
  upcomingProcedures,
  encounterAmendments,
  nativeLabSample,
  procedureTemplateConsumables,
  procedureConsumableBatches,
} as const;

// Authored profiles must never point at a patient that does not exist, except
// for the explicit guardian-consent minor fixture.
for (const profile of patientProfiles) {
  if (profile.patientId.startsWith("SAMPLE-")) continue;
  if (!patientById(profile.patientId)) {
    throw new Error(
      `authored profile references unknown patient ${profile.patientId}`,
    );
  }
}
