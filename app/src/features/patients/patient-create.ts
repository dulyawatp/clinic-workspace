/**
 * PAT-02 create model: the designed ten-field registration form.
 *
 * The prototype (`assets/js/patients.js` → `registration()`) is the copy and
 * field authority: first/last name, gender including "other", birth date,
 * mobile, optional email, allergy status + name, address and emergency
 * contact. Duplicate detection and the local draft are pure client-side rules
 * so they can be tested without a transport.
 *
 * The current native create contract carries only first/last name, sex and
 * birth date. Every other field here is a local draft field: it is never added
 * to the wire payload, which is why the page labels the form as sample data.
 */

export const CREATE_DRAFT_KEY = "clinic.patientCreateDraft.v1";
export const CREATE_SAMPLES_KEY = "clinic.patientCreateSamples.v1";

export type CreateSex = "" | "female" | "male" | "other";
export type AllergyStatus = "unknown" | "none" | "known";

export interface PatientCreateInput {
  readonly firstName: string;
  readonly lastName: string;
  readonly sex: CreateSex;
  readonly dob: string;
  readonly phone: string;
  readonly email: string;
  readonly allergyStatus: AllergyStatus;
  readonly allergyText: string;
  readonly address: string;
  readonly emergency: string;
}

export type CreateField = keyof PatientCreateInput;

export function emptyCreateInput(): PatientCreateInput {
  return {
    firstName: "",
    lastName: "",
    sex: "",
    dob: "",
    phone: "",
    email: "",
    allergyStatus: "unknown",
    allergyText: "",
    address: "",
    emergency: "",
  };
}

export interface CreateFieldError {
  readonly field: CreateField;
  /** Catalogue key: copy lives in the i18n catalogues, never in the model. */
  readonly key: string;
}

/**
 * Prototype rule: only separators are stripped (`081-234-5678` → `0812345678`),
 * so a stray letter still fails the 9–10 digit mobile pattern.
 */
export function normalizePhone(value: string): string {
  return value.replace(/[\s-]/g, "");
}

/** Case- and whitespace-insensitive name identity, never a fuzzy match. */
export function normalizeName(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Bangkok calendar day: the clinic's day, not the browser's local midnight. */
export function bangkokTodayKey(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Bangkok",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Calendar-valid ISO date check; `2023-02-29` is not a birth date. */
export function isCalendarDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText] = match;
  if (yearText === undefined || monthText === undefined || dayText === undefined)
    return false;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

/**
 * Field validation in prototype order. `todayKey` is the Bangkok calendar day
 * so a future birth date is rejected against the clinic's day, not the
 * browser's local midnight.
 */
export function validatePatientCreate(
  input: PatientCreateInput,
  todayKey: string,
): CreateFieldError[] {
  const errors: CreateFieldError[] = [];
  if (!input.firstName.trim())
    errors.push({ field: "firstName", key: "patientCreate.error.required" });
  if (!input.lastName.trim())
    errors.push({ field: "lastName", key: "patientCreate.error.required" });
  if (input.sex === "")
    errors.push({ field: "sex", key: "patientCreate.error.required" });
  if (!input.dob.trim())
    errors.push({ field: "dob", key: "patientCreate.error.required" });
  else if (!isCalendarDate(input.dob))
    errors.push({ field: "dob", key: "patientCreate.error.dob" });
  else if (input.dob > todayKey)
    errors.push({ field: "dob", key: "patientCreate.error.dobFuture" });
  if (!input.phone.trim())
    errors.push({ field: "phone", key: "patientCreate.error.required" });
  else {
    const digits = normalizePhone(input.phone);
    if (!/^0\d{8,9}$/.test(digits))
      errors.push({ field: "phone", key: "patientCreate.error.phone" });
  }
  if (input.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email))
    errors.push({ field: "email", key: "patientCreate.error.email" });
  if (input.allergyStatus === "known" && !input.allergyText.trim())
    errors.push({ field: "allergyText", key: "patientCreate.error.allergy" });
  return errors;
}

/** One row the addressed directory exposes. Phone is masked or absent. */
export interface DirectoryCandidate {
  readonly id: string;
  readonly name: string;
  readonly phone?: string;
}

/**
 * Prototype rule: same mobile or same given + family name is a duplicate
 * candidate, never an automatic merge. A masked phone (`081-XXX-2233`) is not
 * comparable and is skipped instead of being treated as a match.
 */
export function findDuplicateCandidates(
  input: PatientCreateInput,
  candidates: readonly DirectoryCandidate[],
): DirectoryCandidate[] {
  const name = normalizeName(`${input.firstName} ${input.lastName}`);
  const phone = normalizePhone(input.phone);
  return candidates.filter((candidate) => {
    if (name !== "" && normalizeName(candidate.name) === name) return true;
    if (phone === "" || candidate.phone === undefined) return false;
    if (/x/i.test(candidate.phone)) return false;
    return normalizePhone(candidate.phone) === phone;
  });
}

interface StoredDraft {
  readonly version: 1;
  readonly savedAt: string;
  readonly input: PatientCreateInput;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function coerceInput(value: unknown): PatientCreateInput | null {
  if (!isRecord(value)) return null;
  const base = emptyCreateInput();
  const sex = value.sex;
  const allergyStatus = value.allergyStatus;
  if (sex !== undefined && !["", "female", "male", "other"].includes(String(sex)))
    return null;
  if (
    allergyStatus !== undefined &&
    !["unknown", "none", "known"].includes(String(allergyStatus))
  )
    return null;
  const text = (key: CreateField): string =>
    typeof value[key] === "string" ? (value[key] as string) : base[key] as string;
  return {
    firstName: text("firstName"),
    lastName: text("lastName"),
    sex: (sex ?? "") as CreateSex,
    dob: text("dob"),
    phone: text("phone"),
    email: text("email"),
    allergyStatus: (allergyStatus ?? "unknown") as AllergyStatus,
    allergyText: text("allergyText"),
    address: text("address"),
    emergency: text("emergency"),
  };
}

/** A stored draft is restored only when it still matches this schema. */
export function readCreateDraft(): PatientCreateInput | null {
  try {
    const raw = localStorage.getItem(CREATE_DRAFT_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.version !== 1) return null;
    return coerceInput(parsed.input);
  } catch {
    return null;
  }
}

export function writeCreateDraft(input: PatientCreateInput): boolean {
  try {
    const stored: StoredDraft = {
      version: 1,
      savedAt: new Date().toISOString(),
      input,
    };
    localStorage.setItem(CREATE_DRAFT_KEY, JSON.stringify(stored));
    return true;
  } catch {
    return false;
  }
}

export function clearCreateDraft(): void {
  try {
    localStorage.removeItem(CREATE_DRAFT_KEY);
  } catch {
    // A full or blocked storage area must not break the form.
  }
}

export interface SampleRegistration {
  readonly id: string;
  readonly displayName: string;
  readonly phone: string;
  /** The whole designed form, including the emergency contact and address. */
  readonly details: PatientCreateInput;
  readonly createdAt: string;
  readonly sample: true;
}

/**
 * Browser-local demo record. It is stored under its own key and never becomes
 * a directory row: the prototype's "fictional data in this browser only"
 * promise, without pretending a server write happened.
 */
export function saveSampleRegistration(
  input: PatientCreateInput,
): SampleRegistration | null {
  const displayName = `${input.firstName.trim()} ${input.lastName.trim()}`.trim();
  const createdAt = new Date().toISOString();
  try {
    const raw = localStorage.getItem(CREATE_SAMPLES_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    const rows = Array.isArray(parsed) ? parsed : [];
    const record: SampleRegistration = {
      id: `SAMPLE-REG-${String(rows.length + 1).padStart(3, "0")}`,
      displayName,
      phone: input.phone,
      details: input,
      createdAt,
      sample: true,
    };
    localStorage.setItem(CREATE_SAMPLES_KEY, JSON.stringify([...rows, record]));
    return record;
  } catch {
    return null;
  }
}
