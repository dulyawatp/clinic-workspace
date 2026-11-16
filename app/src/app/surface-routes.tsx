import type { ReactElement } from "react";
import type { SurfaceId } from "../design/surfaces";
import { AppointmentPage } from "../features/appointments/AppointmentPage";
import { CalendarPage } from "../features/appointments/CalendarPage";
import { AdminClinicalPage } from "../features/admin/AdminClinicalPage";
import { AdminHomePage } from "../features/admin/AdminHomePage";
import { AdminSettingsPage } from "../features/admin/AdminSettingsPage";
import { AdminSystemPage } from "../features/admin/AdminSystemPage";
import { AdminUsersPage } from "../features/admin/AdminUsersPage";
import { DoctorHomePage } from "../features/clinical/DoctorHomePage";
import { LabWorkspacePage } from "../features/clinical/LabWorkspacePage";
import { ProcedureDetailPage } from "../features/clinical/ProcedureDetailPage";
import { VisitWorkspacePage } from "../features/clinical/VisitWorkspacePage";
import { CashierPage } from "../features/finance/CashierPage";
import { InvoicesPage } from "../features/finance/InvoicesPage";
import { PaymentsPage } from "../features/finance/PaymentsPage";
import { RoleHomePage } from "../features/home/RoleHomePage";
import { ManagementHomePage } from "../features/management/ManagementHomePage";
import { ManagementReportsPage } from "../features/management/ManagementReportsPage";
import { NurseHomePage } from "../features/nursing/NurseHomePage";
import { NurseTasksPage } from "../features/nursing/NurseTasksPage";
import { PatientCreatePage } from "../features/patients/PatientCreatePage";
import { PatientDirectoryPage } from "../features/patients/PatientDirectoryPage";
import { PatientProfilePage } from "../features/patients/PatientProfilePage";
import { PharmacyBalancesPage } from "../features/pharmacy/PharmacyBalancesPage";
import { PharmacyBatchesPage } from "../features/pharmacy/PharmacyBatchesPage";
import { PharmacyItemsPage } from "../features/pharmacy/PharmacyItemsPage";
import { PharmacyMovementsPage } from "../features/pharmacy/PharmacyMovementsPage";
import { PharmacyPage } from "../features/pharmacy/PharmacyPage";
import { PharmacyPurchasingPage } from "../features/pharmacy/PharmacyPurchasingPage";
import { PharmacyReceivePage } from "../features/pharmacy/PharmacyReceivePage";
import { LivePhotoWorkspace } from "../features/photos/LivePhotoWorkspace";
import {
  PhotoComparePage,
  PhotoGalleryPage,
} from "../features/photos/PhotoPages";
import { ProfilePage } from "../features/profile/ProfilePage";
import { CheckoutPage } from "../features/reception/CheckoutPage";
import { QueuePage } from "../features/reception/QueuePage";
import { ReceptionHomePage } from "../features/reception/ReceptionHomePage";
import { SearchPage } from "../features/search/SearchPage";
import { SystemStatesPage } from "../features/system/SystemStatesPage";
import { ROUTES } from "./routes";

export interface SurfaceRoute {
  /** Design surface this path renders. */
  readonly surface: SurfaceId;
  readonly path: string;
  readonly element: ReactElement;
}

/**
 * Every authenticated design surface and the path it is addressed at. The
 * router derives its children from this table, so route/guard coverage stays
 * checkable against the design manifest instead of drifting path by path.
 */
export const SURFACE_ROUTES: readonly SurfaceRoute[] = [
  { surface: "APP-02", path: ROUTES.home, element: <RoleHomePage /> },
  { surface: "APP-02", path: ROUTES.profile, element: <ProfilePage /> },
  { surface: "APP-03", path: ROUTES.search, element: <SearchPage /> },
  { surface: "APP-04", path: ROUTES.system, element: <SystemStatesPage /> },

  {
    surface: "PAT-01",
    path: ROUTES.patients,
    element: <PatientDirectoryPage />,
  },
  {
    surface: "PAT-02",
    path: ROUTES.patientNew,
    element: <PatientCreatePage />,
  },
  {
    surface: "PAT-03",
    path: ROUTES.patientPattern,
    element: <PatientProfilePage />,
  },
  {
    surface: "PAT-04",
    path: ROUTES.photoPattern,
    element: <PhotoGalleryPage />,
  },
  {
    surface: "PAT-04",
    path: ROUTES.livePhotosPatientPattern,
    element: <LivePhotoWorkspace />,
  },
  {
    surface: "PAT-05",
    path: ROUTES.photoComparePattern,
    element: <PhotoComparePage />,
  },

  { surface: "REC-01", path: ROUTES.reception, element: <ReceptionHomePage /> },
  { surface: "REC-02", path: ROUTES.queue, element: <QueuePage /> },
  { surface: "REC-03", path: ROUTES.appointments, element: <CalendarPage /> },
  {
    surface: "REC-04",
    path: ROUTES.appointmentPattern,
    element: <AppointmentPage />,
  },
  { surface: "REC-05", path: ROUTES.checkout, element: <CheckoutPage /> },

  { surface: "DOC-01", path: ROUTES.doctor, element: <DoctorHomePage /> },
  {
    surface: "DOC-02",
    path: ROUTES.visitPattern,
    element: <VisitWorkspacePage />,
  },
  {
    surface: "DOC-03",
    path: ROUTES.procedurePattern,
    element: <ProcedureDetailPage />,
  },
  { surface: "DOC-04", path: ROUTES.labPattern, element: <LabWorkspacePage /> },

  { surface: "NUR-01", path: ROUTES.nurse, element: <NurseHomePage /> },
  { surface: "NUR-02", path: ROUTES.nurseTasks, element: <NurseTasksPage /> },

  { surface: "PHA-01", path: ROUTES.pharmacy, element: <PharmacyPage /> },
  {
    surface: "PHA-02",
    path: ROUTES.pharmacyItems,
    element: <PharmacyItemsPage />,
  },
  {
    surface: "PHA-03",
    path: ROUTES.pharmacyBatches,
    element: <PharmacyBatchesPage />,
  },
  {
    surface: "PHA-04",
    path: ROUTES.pharmacyBalances,
    element: <PharmacyBalancesPage />,
  },
  {
    surface: "PHA-05",
    path: ROUTES.pharmacyReceive,
    element: <PharmacyReceivePage />,
  },
  {
    surface: "PHA-06",
    path: ROUTES.pharmacyMovements,
    element: <PharmacyMovementsPage />,
  },
  {
    surface: "PHA-07",
    path: ROUTES.pharmacyPurchasing,
    element: <PharmacyPurchasingPage />,
  },

  {
    surface: "CAS-01",
    path: ROUTES.cashier,
    element: <CashierPage />,
  },
  {
    surface: "CAS-02",
    path: ROUTES.invoices,
    element: <InvoicesPage />,
  },
  {
    surface: "CAS-03",
    path: ROUTES.payments,
    element: <PaymentsPage />,
  },

  {
    surface: "MGR-01",
    path: ROUTES.management,
    element: <ManagementHomePage />,
  },
  {
    surface: "MGR-02",
    path: ROUTES.managementReports,
    element: <ManagementReportsPage />,
  },

  {
    surface: "ADM-01",
    path: ROUTES.admin,
    element: <AdminHomePage />,
  },
  {
    surface: "ADM-02",
    path: ROUTES.adminUsers,
    element: <AdminUsersPage />,
  },
  {
    surface: "ADM-03",
    path: ROUTES.adminClinical,
    element: <AdminClinicalPage />,
  },
  {
    surface: "ADM-04",
    path: ROUTES.adminSettings,
    element: <AdminSettingsPage />,
  },
  {
    surface: "ADM-05",
    path: ROUTES.adminSystem,
    element: <AdminSystemPage />,
  },
];
