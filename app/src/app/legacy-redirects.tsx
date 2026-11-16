import { Navigate, useParams } from "react-router-dom";
import { ROUTES } from "./routes";
import { useSession } from "./SessionProvider";

/**
 * `/clinic/finance` was a React-era aggregate with no design surface. The
 * designed destinations are the cashier surfaces (billing roles) and management
 * (owner/manager), so an existing deep link lands on the right one.
 */
export function FinanceRedirect() {
  const { session } = useSession();
  const roles = session?.user.roles ?? [];
  const target = roles.some((role) => role === "CAS" || role === "REC")
    ? ROUTES.cashier
    : roles.includes("MGR")
      ? ROUTES.management
      : ROUTES.home;
  return <Navigate to={target} replace />;
}

/**
 * The synthetic lab-photo workspace moved under its patient (PAT-04). The old
 * addressed URL keeps working by forwarding to the designed address; the
 * patient-less legacy index still renders the workspace's own patient picker.
 */
export function LivePhotoRedirect() {
  const { id } = useParams();
  return (
    <Navigate
      to={id ? ROUTES.livePhotosPatient(id) : ROUTES.livePhotos}
      replace
    />
  );
}
