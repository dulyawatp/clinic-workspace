/**
 * Demo/Sample fixtures — part 1: people, appointments and encounters.
 *
 * Ported from the OpenDesign "Clinic Application Design" prototype
 * (`assets/js/data-core.js`), not embedded as runtime code:
 *  - LCG seed 20261103 for the generated rows (180 patients, appointment grid,
 *    historical encounters),
 *  - relative dates anchored to the single `now` captured at module build,
 *  - names and clinical text are fictional.
 *
 * Frappe/Marley vocabulary (task 2 brief): rows carry the installed DocType
 * they model (`Patient`, `Patient Appointment`, `Patient Encounter`,
 * `Healthcare Practitioner`, `Healthcare Service Unit`). The prototype's
 * localized strings are kept as authored Thai sample text; generated rows
 * reference an i18n catalogue key instead of baking a locale.
 *
 * Never treat these rows as backend truth: every row has `sample: true` and
 * the module exports `SAMPLE_LABEL`.
 */

export const SAMPLE_LABEL =
  "Demo/Sample data — fictional clinic rows; not a Marley backend contract";

export const CLINIC_TIME_ZONE = "Asia/Bangkok";
export const SEED_CORE = 20261103;

export interface L10n {
  readonly th: string;
  readonly en: string;
}

export interface Practitioner {
  readonly id: string;
  readonly docType: "Healthcare Practitioner";
  readonly name: L10n;
  readonly dept: L10n;
  readonly color: "teal" | "sage" | "ink";
  readonly sample: true;
}

export interface ApptType {
  readonly id: string;
  readonly docType: "Appointment Type";
  /** i18n key for the designed label (`appt.type.*`). */
  readonly key: string;
  readonly minutes: number;
  readonly price: number;
  readonly sample: true;
}

export interface Room {
  readonly id: string;
  readonly docType: "Healthcare Service Unit";
  readonly name: L10n;
  readonly isGroup: boolean;
  readonly sample: true;
}

export interface Allergy {
  readonly status: "none" | "known" | "unknown";
  readonly items: readonly L10n[];
}

/**
 * Prototype consent summary. The full evidence record (signer, evidence,
 * consent date, withdrawal, guardian, expiry) is authored in
 * `data-clinical.ts`; this summary stays for the existing photo chip.
 */
export interface PatientConsentSummary {
  readonly photo: boolean;
  readonly at: string | null;
  readonly version: string | null;
}

export interface Patient {
  readonly id: string;
  readonly docType: "Patient";
  readonly given: L10n;
  readonly family: L10n;
  readonly sex: "female" | "male" | "other";
  readonly dob: string;
  readonly phone: string;
  readonly email: string;
  readonly allergies: Allergy;
  readonly consent: PatientConsentSummary;
  /** Accounting customer link (Sales Invoice.customer). */
  readonly customer: string;
  readonly note?: L10n;
  readonly sample: true;
}

export type AppointmentStatus =
  | "scheduled"
  | "confirmed"
  | "arrived"
  | "checkedIn"
  | "waiting"
  | "withDoctor"
  | "completed"
  | "cancelled"
  | "noShow";

export interface Appointment {
  readonly id: string;
  readonly docType: "Patient Appointment";
  readonly patientId: string;
  readonly date: string;
  readonly duration: number;
  readonly typeId: string;
  readonly practitioner: string;
  readonly room: string;
  readonly status: AppointmentStatus;
  readonly note: L10n | "";
  readonly source: "phone" | "online" | "walkin";
  readonly checkedInAt: string | null;
  readonly visitId: string | null;
  readonly sample: true;
}

export interface Vitals {
  readonly temp: number;
  readonly pulse: number;
  readonly resp: number;
  readonly sys: number;
  readonly dia: number;
  readonly height: number;
  readonly weight: number;
  readonly recordedAt: string;
  readonly by: string;
}

export interface Diagnosis {
  readonly code: string;
  readonly primary: boolean;
  readonly label: L10n;
}

export interface MedicationOrder {
  readonly item: string;
  readonly dose: string;
  readonly freq: string;
  readonly route?: string;
  readonly duration: string;
  readonly status: "prescribed" | "administered" | "stopped";
}

export interface ProcedureLine {
  readonly id: string;
  readonly templateKey: string;
  readonly status: "in-progress" | "completed";
  readonly at: string;
  readonly consumables: readonly {
    readonly itemId: string;
    /** Resolved from the stock fixture by `data-ops.resolveConsumableBatch`. */
    readonly batch: null;
    readonly batchPolicy: "oldest-usable";
    readonly qty: number;
    readonly warehouse: string;
  }[];
}

export interface VisitLabLine {
  readonly id: string;
  readonly testKey: string;
  readonly provider: string;
  readonly status: "ordered" | "collected" | "sent" | "received" | "reviewed" | "cancelled";
  readonly orderedAt: string;
  readonly sentAt: string | null;
  readonly receivedAt: string | null;
  readonly reviewedAt: string | null;
  readonly file: string | null;
}

export interface Visit {
  readonly id: string;
  readonly docType: "Patient Encounter";
  readonly patientId: string;
  readonly appointmentId: string | null;
  readonly practitioner: string;
  readonly date: string;
  readonly state: "draft" | "completed";
  readonly draftSavedAt?: string;
  readonly history: {
    readonly complaint?: string;
    /** Catalogue key used by generated rows instead of a baked locale. */
    readonly complaintKey?: string;
    readonly hpi?: string;
    readonly past?: string;
    readonly surgical?: string;
    readonly family?: string;
    readonly reviewedAllergies: boolean;
  };
  readonly exam: {
    readonly vitals?: Vitals;
    readonly area?: string;
    readonly site?: string;
    readonly findings?: string;
    readonly attachments?: number;
  };
  readonly note: {
    readonly assessment?: string;
    readonly assessmentKey?: string;
    readonly plan?: string;
    readonly planKey?: string;
    readonly rationale?: string;
    readonly followup?: string;
    readonly signed: boolean;
  };
  readonly diagnoses: readonly Diagnosis[];
  readonly meds: readonly MedicationOrder[];
  readonly procedures?: readonly ProcedureLine[];
  readonly labs?: readonly VisitLabLine[];
  readonly historical?: true;
  readonly sample: true;
}

export interface ClinicDates {
  /** ISO instant for `offsetDays` from the anchored Bangkok day. */
  at(offsetDays: number, time?: string): string;
  dayKey(iso: string): string;
  todayKey(): string;
  addDays(iso: string, n: number): string;
  weekdayIdx(offset: number): number;
}

/** Bangkok calendar day key (`YYYY-MM-DD`) for an instant. */
export function bangkokDayKey(instant: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: CLINIC_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(instant);
}

/**
 * Relative-date engine. The prototype re-read the wall clock on every call;
 * here one anchor is captured per build so the whole fixture set shares a
 * single day boundary. Production use keeps the live import time; tests freeze
 * it with fake timers.
 */
export function clinicDates(anchor: Date = new Date()): ClinicDates {
  const bangkokDay = bangkokDayKey(anchor);
  return {
    at(offsetDays, time = "09:00") {
      const date = new Date(`${bangkokDay}T${time}:00+07:00`);
      date.setUTCDate(date.getUTCDate() + offsetDays);
      return date.toISOString();
    },
    dayKey(iso) {
      return iso.slice(0, 10);
    },
    todayKey() {
      return bangkokDay;
    },
    addDays(iso, n) {
      const date = new Date(iso);
      date.setDate(date.getDate() + n);
      return date.toISOString();
    },
    weekdayIdx(offset) {
      const date = new Date(anchor);
      date.setDate(date.getDate() + offset);
      return date.getDay();
    },
  };
}

/** Deterministic LCG identical to the prototype's `rng(seed)`. */
export function seededRng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

export interface CoreData {
  /** Single captured clock the whole fixture set is anchored to. */
  readonly now: string;
  readonly dates: ClinicDates;
  readonly patients: Patient[];
  readonly practitioners: Practitioner[];
  readonly apptTypes: ApptType[];
  readonly rooms: Room[];
  readonly appointments: Appointment[];
  readonly visits: Visit[];
  readonly counts: { queue: number; visits: number; tasks: number; bills: number };
  readonly patientById: (id: string) => Patient | null;
  readonly visitById: (id: string) => Visit | null;
  readonly appointmentById: (id: string) => Appointment | null;
  readonly practitionerById: (id: string) => Practitioner;
  readonly appointmentTypeById: (id: string) => ApptType;
  readonly roomById: (id: string) => Room;
  readonly patientName: (id: string, locale: "th" | "en") => string;
  readonly appointmentsOn: (dayKey: string) => Appointment[];
  readonly visitsFor: (patientId: string) => Visit[];
}

const GIVEN_TH = [
  "อรุณี", "ปิยะ", "ศิริพร", "ณัฐพล", "จิราภรณ์", "วีระ", "กมลชนก", "ธนากร",
  "พิมพ์ใจ", "อนุชา", "รัตนา", "สรายุทธ", "เบญจวรรณ", "ภาณุ", "ชลธิชา",
  "เอกชัย", "นภาพร", "ธีรภัทร", "สุพัตรา", "กิตติศักดิ์",
] as const;
const GIVEN_EN = [
  "Arunee", "Piya", "Siriporn", "Nattapon", "Jiraporn", "Weera", "Kamolchanok",
  "Thanakorn", "Pimjai", "Anucha", "Rattana", "Sarayut", "Benjawan", "Phanu",
  "Chonthicha", "Ekachai", "Napaporn", "Teerapat", "Supatra", "Kittisak",
] as const;
const FAMILY_TH = [
  "ใจดี", "ศรีทอง", "วัฒนา", "บุญมี", "แสงเดือน", "รุ่งเรือง", "ทองคำ",
  "พงษ์พันธ์", "อินทรีย์", "สุขสมบูรณ์", "จันทร์เพ็ญ", "ธนวัฒน์", "ศรีสวัสดิ์",
  "มงคล", "สุวรรณ",
] as const;
const FAMILY_EN = [
  "Jaidee", "Sritong", "Wattana", "Boonmee", "Saengduen", "Rungrueng",
  "Thongkham", "Phongphan", "Intree", "Suksomboon", "Chanpen", "Thanawat",
  "Srisawat", "Mongkol", "Suwan",
] as const;

const ALLERGY_NONE: Allergy = { status: "none", items: [] };
const ALLERGY_PENICILLIN: Allergy = {
  status: "known",
  items: [{ th: "เพนิซิลลิน", en: "Penicillin" }],
};
const ALLERGY_LIDOCAINE: Allergy = {
  status: "known",
  items: [{ th: "ลิโดเคน", en: "Lidocaine" }],
};

export function buildCore(now: Date = new Date()): CoreData {
  const dates = clinicDates(now);
  const rand = seededRng(SEED_CORE);
  const int = (min: number, max: number) =>
    Math.floor(rand() * (max - min + 1)) + min;
  const pick = <T>(rows: readonly T[]): T =>
    rows[Math.floor(rand() * rows.length) % rows.length] as T;

  const practitioners: Practitioner[] = [
    {
      id: "HP-01",
      docType: "Healthcare Practitioner",
      name: { th: "นพ. กิตติ วรรณชัย", en: "Dr. Kitti Wannachai" },
      dept: { th: "ผิวหนัง", en: "Dermatology" },
      color: "teal",
      sample: true,
    },
    {
      id: "HP-02",
      docType: "Healthcare Practitioner",
      name: { th: "พญ. ลลิตา ปิ่นทอง", en: "Dr. Lalita Pintong" },
      dept: { th: "ผิวหนังและเลเซอร์", en: "Dermatology & laser" },
      color: "sage",
      sample: true,
    },
    {
      id: "HP-03",
      docType: "Healthcare Practitioner",
      name: { th: "พญ. ศรันยา ภูวเดช", en: "Dr. Saranya Phoowadech" },
      dept: { th: "เวชศาสตร์ aesthetic", en: "Aesthetic medicine" },
      color: "ink",
      sample: true,
    },
  ];

  const apptTypes: ApptType[] = [
    { id: "AT-01", docType: "Appointment Type", key: "appt.type.consult", minutes: 30, price: 800, sample: true },
    { id: "AT-02", docType: "Appointment Type", key: "appt.type.followup", minutes: 20, price: 500, sample: true },
    { id: "AT-03", docType: "Appointment Type", key: "appt.type.laser", minutes: 60, price: 4500, sample: true },
    { id: "AT-04", docType: "Appointment Type", key: "appt.type.injection", minutes: 45, price: 6800, sample: true },
    { id: "AT-05", docType: "Appointment Type", key: "appt.type.peel", minutes: 45, price: 2500, sample: true },
    { id: "AT-06", docType: "Appointment Type", key: "appt.type.acne", minutes: 30, price: 1200, sample: true },
  ];

  const rooms: Room[] = [
    { id: "SU-01", docType: "Healthcare Service Unit", name: { th: "ห้องตรวจ 1", en: "Consult room 1" }, isGroup: false, sample: true },
    { id: "SU-02", docType: "Healthcare Service Unit", name: { th: "ห้องตรวจ 2", en: "Consult room 2" }, isGroup: false, sample: true },
    { id: "SU-03", docType: "Healthcare Service Unit", name: { th: "ห้องเลเซอร์", en: "Laser room" }, isGroup: false, sample: true },
    { id: "SU-04", docType: "Healthcare Service Unit", name: { th: "ห้องหัตถการ", en: "Procedure room" }, isGroup: false, sample: true },
  ];

  // Consent dates are relative to the frozen clock. The prototype hard-coded
  // absolute instants here, which drifted away from every other sample date.
  const patients: Patient[] = [
    {
      id: "P-10241",
      docType: "Patient",
      given: { th: "ณัฐธิดา", en: "Nattida" },
      family: { th: "ศรีสวัสดิ์", en: "Srisawat" },
      sex: "female",
      dob: "1993-04-18",
      phone: "081-234-5678",
      email: "nattida.s@example.com",
      allergies: ALLERGY_NONE,
      consent: { photo: true, at: dates.at(-95, "10:04"), version: "PC-2.1" },
      customer: "CUST-1041",
      note: {
        th: "ผู้ป่วยจำกัดสิทธิ์เข้าถึงภาพถ่ายเฉพาะทีมรักษา",
        en: "Photo access limited to the treating team",
      },
      sample: true,
    },
    {
      id: "P-10242",
      docType: "Patient",
      given: { th: "ปวีณา", en: "Paweena" },
      family: { th: "วัฒนกิจ", en: "Wattanakit" },
      sex: "female",
      dob: "1988-11-02",
      phone: "089-556-1122",
      email: "paweena.w@example.com",
      allergies: ALLERGY_PENICILLIN,
      consent: { photo: true, at: dates.at(-16, "09:20"), version: "PC-2.1" },
      customer: "CUST-1042",
      sample: true,
    },
    {
      id: "P-10243",
      docType: "Patient",
      given: { th: "ธนวัฒน์", en: "Thanawat" },
      family: { th: "รุ่งเรือง", en: "Rungrueng" },
      sex: "male",
      dob: "1979-02-27",
      phone: "086-441-9080",
      email: "thanawat.r@example.com",
      allergies: ALLERGY_NONE,
      consent: { photo: false, at: null, version: null },
      customer: "CUST-1043",
      sample: true,
    },
    {
      id: "P-10244",
      docType: "Patient",
      given: { th: "สุดารัตน์", en: "Sudarat" },
      family: { th: "อินทรีย์", en: "Intree" },
      sex: "female",
      dob: "1996-07-09",
      phone: "085-772-3344",
      email: "",
      allergies: ALLERGY_LIDOCAINE,
      consent: { photo: true, at: dates.at(-14, "14:12"), version: "PC-2.1" },
      customer: "CUST-1044",
      sample: true,
    },
    {
      id: "P-10245",
      docType: "Patient",
      given: { th: "กิตติพงษ์", en: "Kittipong" },
      family: { th: "มงคล", en: "Mongkol" },
      sex: "male",
      dob: "2001-01-30",
      phone: "092-118-6677",
      email: "kittipong.m@example.com",
      allergies: ALLERGY_NONE,
      consent: { photo: true, at: dates.at(-58, "11:30"), version: "PC-2.0" },
      customer: "CUST-1045",
      sample: true,
    },
    {
      id: "P-10246",
      docType: "Patient",
      given: { th: "ชลธิชา", en: "Chonthicha" },
      family: { th: "ทองคำ", en: "Thongkham" },
      sex: "female",
      dob: "1990-06-14",
      phone: "081-909-2233",
      email: "chonthicha.t@example.com",
      allergies: ALLERGY_NONE,
      consent: { photo: true, at: dates.at(-26, "16:45"), version: "PC-2.1" },
      customer: "CUST-1046",
      sample: true,
    },
    {
      id: "P-10247",
      docType: "Patient",
      given: { th: "ภาณุพงศ์", en: "Phanupong" },
      family: { th: "พงษ์พันธ์", en: "Phongphan" },
      sex: "male",
      dob: "1985-03-21",
      phone: "084-330-7788",
      email: "",
      allergies: ALLERGY_NONE,
      consent: { photo: false, at: null, version: null },
      customer: "CUST-1047",
      sample: true,
    },
    {
      id: "P-10248",
      docType: "Patient",
      given: { th: "เบญจวรรณ", en: "Benjawan" },
      family: { th: "จันทร์เพ็ญ", en: "Chanpen" },
      sex: "female",
      dob: "1998-12-05",
      phone: "088-221-4455",
      email: "benjawan.c@example.com",
      allergies: ALLERGY_NONE,
      consent: { photo: true, at: dates.at(-13, "08:55"), version: "PC-2.1" },
      customer: "CUST-1048",
      sample: true,
    },
  ];

  // Generated volume keeps the demo honest: 180 patient records in total.
  for (let i = 0; i < 172; i++) {
    const gi = int(0, GIVEN_TH.length - 1);
    const fi = int(0, FAMILY_TH.length - 1);
    const sex = rand() > 0.32 ? "female" : "male";
    const year = int(1962, 2006);
    const consents = rand() > 0.18;
    patients.push({
      id: `P-${10300 + i}`,
      docType: "Patient",
      given: { th: GIVEN_TH[gi] as string, en: GIVEN_EN[gi] as string },
      family: { th: FAMILY_TH[fi] as string, en: FAMILY_EN[fi] as string },
      sex,
      dob: `${year}-${String(int(1, 12)).padStart(2, "0")}-${String(int(1, 28)).padStart(2, "0")}`,
      phone: `0${int(6, 9)}-${int(100, 999)}-${int(1000, 9999)}`,
      email: "",
      allergies: rand() > 0.86 ? ALLERGY_PENICILLIN : ALLERGY_NONE,
      consent: consents
        ? { photo: true, at: dates.at(-int(10, 300), "10:00"), version: "PC-2.1" }
        : { photo: false, at: null, version: null },
      customer: `CUST-${1100 + i}`,
      sample: true,
    });
  }

  const appointmentTypeById = (id: string): ApptType =>
    apptTypes.find((type) => type.id === id) ?? (apptTypes[0] as ApptType);

  const appointments: Appointment[] = [];

  const addAppointment = (input: {
    id: string;
    patientId: string;
    date: string;
    typeId?: string;
    duration?: number;
    practitioner?: string;
    room?: string;
    status?: AppointmentStatus;
    note?: L10n | "";
    source?: Appointment["source"];
    checkedInAt?: string | null;
    visitId?: string | null;
  }) => {
    const type = appointmentTypeById(input.typeId ?? "AT-01");
    appointments.push({
      id: input.id,
      docType: "Patient Appointment",
      patientId: input.patientId,
      date: input.date,
      duration: input.duration ?? type.minutes,
      typeId: type.id,
      practitioner: input.practitioner ?? (practitioners[0] as Practitioner).id,
      room: input.room ?? "SU-01",
      status: input.status ?? "scheduled",
      note: input.note ?? "",
      source: input.source ?? "phone",
      checkedInAt: input.checkedInAt ?? null,
      visitId: input.visitId ?? null,
      sample: true,
    });
  };

  // Today's desk — the rows front desk staff actually work with.
  addAppointment({ id: "DC-APT-3301", patientId: "P-10241", date: dates.at(0, "09:30"), typeId: "AT-01", status: "withDoctor", checkedInAt: dates.at(0, "09:12"), practitioner: "HP-01", room: "SU-01", visitId: "DC-VIS-2041" });
  addAppointment({ id: "DC-APT-3302", patientId: "P-10242", date: dates.at(0, "09:00"), typeId: "AT-02", status: "waiting", checkedInAt: dates.at(0, "08:48"), practitioner: "HP-01", room: "SU-01" });
  addAppointment({ id: "DC-APT-3303", patientId: "P-10243", date: dates.at(0, "10:00"), typeId: "AT-03", status: "checkedIn", checkedInAt: dates.at(0, "09:41"), practitioner: "HP-02", room: "SU-03" });
  addAppointment({ id: "DC-APT-3304", patientId: "P-10244", date: dates.at(0, "10:30"), typeId: "AT-04", status: "confirmed", practitioner: "HP-03", room: "SU-04" });
  addAppointment({ id: "DC-APT-3305", patientId: "P-10245", date: dates.at(0, "11:00"), typeId: "AT-05", status: "scheduled", practitioner: "HP-02", room: "SU-03" });
  addAppointment({ id: "DC-APT-3306", patientId: "P-10246", date: dates.at(0, "11:30"), typeId: "AT-06", status: "arrived", checkedInAt: dates.at(0, "11:22"), practitioner: "HP-01", room: "SU-02" });
  addAppointment({ id: "DC-APT-3307", patientId: "P-10247", date: dates.at(0, "13:00"), typeId: "AT-01", status: "scheduled", practitioner: "HP-03", room: "SU-01" });
  addAppointment({ id: "DC-APT-3308", patientId: "P-10248", date: dates.at(0, "13:30"), typeId: "AT-02", status: "confirmed", practitioner: "HP-01", room: "SU-01" });
  addAppointment({ id: "DC-APT-3309", patientId: "P-10301", date: dates.at(0, "14:00"), typeId: "AT-03", status: "scheduled", practitioner: "HP-02", room: "SU-03" });
  addAppointment({ id: "DC-APT-3310", patientId: "P-10302", date: dates.at(0, "14:30"), typeId: "AT-01", status: "cancelled", practitioner: "HP-01", room: "SU-01", note: { th: "ผู้ป่วยขอเลื่อนนัด", en: "Patient asked to move the visit" } });
  addAppointment({ id: "DC-APT-3311", patientId: "P-10303", date: dates.at(0, "15:00"), typeId: "AT-04", status: "scheduled", practitioner: "HP-03", room: "SU-04" });
  addAppointment({ id: "DC-APT-3312", patientId: "P-10304", date: dates.at(0, "15:30"), typeId: "AT-05", status: "confirmed", practitioner: "HP-02", room: "SU-03" });
  addAppointment({ id: "DC-APT-3313", patientId: "P-10305", date: dates.at(0, "16:00"), typeId: "AT-02", status: "scheduled", practitioner: "HP-01", room: "SU-01" });
  addAppointment({ id: "DC-APT-3314", patientId: "P-10306", date: dates.at(0, "16:30"), typeId: "AT-01", status: "noShow", practitioner: "HP-03", room: "SU-01" });
  addAppointment({ id: "DC-APT-3315", patientId: "P-10307", date: dates.at(0, "17:00"), typeId: "AT-06", status: "completed", checkedInAt: dates.at(0, "16:52"), practitioner: "HP-01", room: "SU-02", visitId: "DC-VIS-2042" });
  addAppointment({ id: "DC-APT-3316", patientId: "P-10308", date: dates.at(0, "17:30"), typeId: "AT-02", status: "completed", checkedInAt: dates.at(0, "17:20"), practitioner: "HP-02", room: "SU-01", visitId: "DC-VIS-2043" });

  // Past 13 days + next 6 days, generated coherently.
  const slots = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"] as const;
  let auto = 0;
  for (let d = -13; d <= 6; d++) {
    if (d === 0) continue;
    const count = d < 0 ? int(7, 13) : int(4, 9);
    for (let k = 0; k < count; k++) {
      auto++;
      const patient = patients[int(8, patients.length - 1)] as Patient;
      const type = apptTypes[int(0, apptTypes.length - 1)] as ApptType;
      const slot = slots[int(0, slots.length - 1)] as string;
      let status: AppointmentStatus;
      if (d < 0) status = rand() > 0.14 ? "completed" : rand() > 0.5 ? "cancelled" : "noShow";
      else status = rand() > 0.35 ? "scheduled" : "confirmed";
      addAppointment({
        id: `DC-APT-${4000 + auto}`,
        patientId: patient.id,
        date: dates.at(d, slot),
        typeId: type.id,
        practitioner: (practitioners[int(0, 2)] as Practitioner).id,
        room: (rooms[int(0, 3)] as Room).id,
        status,
        source: pick(["phone", "online", "walkin"] as const),
        checkedInAt: status === "completed" ? dates.at(d, slot) : null,
        visitId: status === "completed" ? `DC-VIS-${5000 + auto}` : null,
      });
    }
  }

  const visits: Visit[] = [];

  visits.push({
    id: "DC-VIS-2041",
    docType: "Patient Encounter",
    patientId: "P-10241",
    appointmentId: "DC-APT-3301",
    practitioner: "HP-01",
    date: dates.at(0, "09:35"),
    state: "draft",
    draftSavedAt: dates.at(0, "09:52"),
    history: {
      complaint: "ผมร่วงบริเวณกลางศีรษะมากขึ้นในช่วง 3 เดือนที่ผ่านมา",
      hpi: "ผู้ป่วยสังเกตว่าผมบางลงเรื่อย ๆ โดยเฉพาะด้านหน้าตรงกลาง ไม่มีอาการคันหรือเจ็บ ไม่มีผื่น ไม่ได้เปลี่ยนผลิตภัณฑ์ดูแลเส้นผม เริ่มใช้มินอกซิดิล 5% เมื่อ 2 เดือนก่อน สม่ำเสมอประมาณ 5 วัน/สัปดาห์",
      past: "ไม่มีโรคประจำตัวสำคัญ เคยตรวจเลือดปี 2568 ผลปกติ",
      surgical: "ไม่มีประวัติผ่าตัด",
      family: "บิดามีประวัติผมร่วงตามอายุ",
      reviewedAllergies: true,
    },
    exam: {
      vitals: { temp: 36.6, pulse: 78, resp: 16, sys: 118, dia: 76, height: 168, weight: 61, recordedAt: dates.at(0, "09:25"), by: "NUR" },
      area: "scalp",
      site: "กลางศีรษะด้านหน้า (frontal scalp)",
      findings: "หนังศีรษะไม่มีผื่นแดง ไม่มีรอยแผลเป็น ความหนาแน่นเส้นผมบริเวณกลางศีรษะลดลงชัดเจนเมื่อเทียบกับด้านข้าง ไม่พบ hair pull test เป็นบวก ไม่พบต่อมไทรอยด์โต",
      attachments: 0,
    },
    note: {
      assessment: "เข้าข่าย Androgenetic alopecia ระยะเริ่มต้น ร่วมกับปัจจัยกดการเจริญของเส้นผมจากความเครียดและการพักผ่อนน้อย ไม่พบสัญญาณของโรคผิวหนังอักเสบหรือติดเชื้อ",
      plan: "คงมินอกซิดิล 5% วันละ 1 มล. ต่อเนื่อง เริ่มเลเซอร์กระตุ้นรากผม 4 ครั้ง ห่างกัน 2 สัปดาห์ ตรวจเลือดคัดกรองภาวะขาดธาตุเหล็กและไทรอยด์ นัดติดตาม 6 สัปดาห์ พร้อมถ่ายภาพเปรียบเทียบมุมเดิม",
      rationale: "เลือกการรักษาแบบไม่รุกรานก่อน เพราะยังอยู่ในระยะเริ่มต้นและผู้ป่วยให้น้ำหนักกับการกลับไปทำงานได้ทันที",
      followup: "นัดติดตาม 6 สัปดาห์",
      signed: false,
    },
    diagnoses: [
      { code: "L65.9", primary: true, label: { th: "ผมร่วงแบบไม่ทราบสาเหตุ (Androgenetic)", en: "Nonscarring hair loss (androgenetic pattern)" } },
    ],
    meds: [
      { item: "Minoxidil 5% solution 60 ml", dose: "1 มล.", freq: "2 ครั้ง/วัน", route: "ทาภายนอก", duration: "90 วัน", status: "prescribed" },
    ],
    procedures: [
      {
        id: "DC-PROC-118",
        templateKey: "proc.laserHair",
        status: "in-progress",
        at: dates.at(0, "10:10"),
        // The prototype hard-coded lot B-2411, which no batch actually owns.
        // The lot is resolved from the stock fixture at read time instead.
        consumables: [
          { itemId: "IT-07", batch: null, batchPolicy: "oldest-usable", qty: 1, warehouse: "WH-ROOM" },
        ],
      },
    ],
    labs: [
      { id: "DC-LAB-77", testKey: "lab.cbcFerritin", provider: "Bangkok Central Lab", status: "received", orderedAt: dates.at(0, "09:40"), sentAt: dates.at(0, "10:05"), receivedAt: dates.at(0, "11:05"), reviewedAt: null, file: "result-77.pdf" },
    ],
    sample: true,
  });

  // Completed visits today (linked to invoices).
  visits.push({
    id: "DC-VIS-2042",
    docType: "Patient Encounter",
    patientId: "P-10307",
    appointmentId: "DC-APT-3315",
    practitioner: "HP-01",
    date: dates.at(0, "17:05"),
    state: "completed",
    history: { complaint: "สิวอักเสบบริเวณกราม", reviewedAllergies: true },
    exam: {
      vitals: { temp: 36.7, pulse: 74, resp: 16, sys: 115, dia: 72, height: 172, weight: 70, recordedAt: dates.at(0, "16:58"), by: "NUR" },
      area: "face",
      findings: "สิวอักเสบ 6 จุดบริเวณกรามทั้งสองข้าง ไม่พบถุงหนองขนาดใหญ่",
    },
    note: { assessment: "Acne vulgaris mild-moderate", plan: "ยาทาเฉพาะที่และนัดติดตาม 4 สัปดาห์", signed: true },
    diagnoses: [{ code: "L70.0", primary: true, label: { th: "สิวสามัญ", en: "Acne vulgaris" } }],
    meds: [{ item: "Adapalene 0.1% gel", dose: "แต้มบาง ๆ", freq: "ก่อนนอน", duration: "30 วัน", status: "prescribed" }],
    sample: true,
  });
  visits.push({
    id: "DC-VIS-2043",
    docType: "Patient Encounter",
    patientId: "P-10308",
    appointmentId: "DC-APT-3316",
    practitioner: "HP-02",
    date: dates.at(0, "17:35"),
    state: "completed",
    history: { complaint: "ติดตามผลหลังเลเซอร์", reviewedAllergies: true },
    exam: {
      vitals: { temp: 36.5, pulse: 72, resp: 16, sys: 110, dia: 70, height: 160, weight: 54, recordedAt: dates.at(0, "17:24"), by: "NUR" },
      area: "face",
      findings: "ผิวหลังเลเซอร์ไม่มีรอยแดงรุนแรง",
    },
    note: { assessment: "ติดตามผลหลังเลเซอร์ สภาพผิวดีขึ้นตามคาด", plan: "นัดครั้งถัดไป 4 สัปดาห์", signed: true },
    diagnoses: [],
    meds: [],
    sample: true,
  });

  // Historical visits for generated completed appointments.
  for (const appointment of appointments) {
    if (
      appointment.visitId &&
      !["DC-VIS-2041", "DC-VIS-2042", "DC-VIS-2043"].includes(appointment.visitId)
    ) {
      const type = appointmentTypeById(appointment.typeId);
      visits.push({
        id: appointment.visitId,
        docType: "Patient Encounter",
        patientId: appointment.patientId,
        appointmentId: appointment.id,
        practitioner: appointment.practitioner,
        date: appointment.date,
        state: "completed",
        history: { complaintKey: type.key, reviewedAllergies: true },
        exam: {
          vitals: {
            temp: 36.4 + int(0, 6) / 10,
            pulse: 68 + int(0, 16),
            resp: 15 + int(0, 3),
            sys: 108 + int(0, 18),
            dia: 68 + int(0, 12),
            height: 155 + int(0, 25),
            weight: 50 + int(0, 30),
            recordedAt: appointment.date,
            by: "NUR",
          },
          area: pick(["face", "scalp", "body"]),
        },
        note: {
          assessmentKey: "visit.historical.assessment",
          planKey: "visit.historical.plan",
          signed: true,
        },
        diagnoses: [],
        meds: [],
        historical: true,
        sample: true,
      });
    }
  }

  const patientById = (id: string) =>
    patients.find((patient) => patient.id === id) ?? null;
  const visitById = (id: string) => visits.find((visit) => visit.id === id) ?? null;
  const appointmentById = (id: string) =>
    appointments.find((appointment) => appointment.id === id) ?? null;

  return {
    now: now.toISOString(),
    dates,
    patients,
    practitioners,
    apptTypes,
    rooms,
    appointments,
    visits,
    counts: {
      queue: appointments.filter(
        (appointment) =>
          appointment.date.slice(0, 10) === dates.todayKey() &&
          ["arrived", "checkedIn", "waiting"].includes(appointment.status),
      ).length,
      visits: visits.filter((visit) => visit.state === "draft").length,
      tasks: 7,
      // Filled by `data-ops`; kept for the prototype's count shape.
      bills: 0,
    },
    patientById,
    visitById,
    appointmentById,
    practitionerById: (id) =>
      practitioners.find((practitioner) => practitioner.id === id) ??
      (practitioners[0] as Practitioner),
    appointmentTypeById,
    roomById: (id) => rooms.find((room) => room.id === id) ?? (rooms[0] as Room),
    patientName: (id, locale) => {
      const patient = patientById(id);
      if (!patient) return "—";
      return `${patient.given[locale]} ${patient.family[locale]}`;
    },
    appointmentsOn: (dayKey) =>
      appointments
        .filter((appointment) => appointment.date.slice(0, 10) === dayKey)
        .sort((a, b) => (a.date < b.date ? -1 : 1)),
    visitsFor: (patientId) =>
      visits
        .filter((visit) => visit.patientId === patientId)
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
  };
}

/**
 * Build-time default. Callers that need a deterministic set (tests, SSR)
 * should use `buildCore(frozenDate)` — or import after freezing the clock.
 */
export const coreData: CoreData = buildCore();
export const now = coreData.now;
export const patients = coreData.patients;
export const appointments = coreData.appointments;
export const visits = coreData.visits;
export const practitioners = coreData.practitioners;
export const apptTypes = coreData.apptTypes;
export const rooms = coreData.rooms;
export const counts = coreData.counts;
/**
 * Flat convenience re-export of `counts.bills`. The prototype core shape keeps
 * this at 0 until invoices exist; the canonical invoice-derived count is
 * `data-ops.counts.bills` (26 on the seeded set).
 */
export const bills = counts.bills;
export const patientById = coreData.patientById;
export const visitById = coreData.visitById;
export const appointmentById = coreData.appointmentById;
export const practitionerById = coreData.practitionerById;
export const appointmentTypeById = coreData.appointmentTypeById;
export const roomById = coreData.roomById;
export const patientName = coreData.patientName;
export const appointmentsOn = coreData.appointmentsOn;
export const visitsFor = coreData.visitsFor;

/** Clinical originals are user-selected only; no fabricated images. */
export const photos: readonly never[] = [];
