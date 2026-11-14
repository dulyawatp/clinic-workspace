import type { FastifyInstance } from "fastify";
import { registerFileRoutes } from "../read/file-routes.js";
import { registerReadRoutes } from "../read/handler.js";
import type { EncounterDraftStore } from "../writes/encounter-draft-store.js";
import type { EncounterReviewStore } from "../writes/encounter-review-store.js";
import type { PatientCreateStore } from "../writes/patient-create-store.js";
import { type AuthDeps, registerAuthRoutes } from "./auth.js";
import { registerEncounterDraftRoutes } from "./encounter-draft.js";
import { registerEncounterReviewRoutes } from "./encounter-review.js";
import { registerHealthRoutes } from "./health.js";
import { registerLiveAppointmentRoutes } from "./live-appointments.js";
import { registerLiveEncounterDraftRoutes } from "./live-encounter-drafts.js";
import { registerLiveFinanceRoutes } from "./live-finance.js";
import { registerLiveMetadataRoutes } from "./live-metadata.js";
import { registerLivePhotoRoutes } from "./live-photos.js";
import { registerLiveStockRoutes } from "./live-stock.js";
import { registerPatientCreateRoutes } from "./patient-create.js";
import { registerPhotoWriteRoutes } from "./photo-save.js";
import { registerSessionRoutes } from "./session.js";

export type RouteDeps = AuthDeps & {
  fetchImpl: typeof fetch;
  patientCreates: PatientCreateStore;
  encounterDrafts: EncounterDraftStore;
  encounterReviews: EncounterReviewStore;
};

/**
 * Single registration point for every proxy route. Each route is registered
 * exactly once; `buildServer` is the only caller.
 */
export function registerRoutes(app: FastifyInstance, deps: RouteDeps): void {
  registerAuthRoutes(app, deps);
  registerHealthRoutes(app, deps);
  registerSessionRoutes(app, deps);
  registerReadRoutes(app, deps);
  registerFileRoutes(app, deps);
  registerPhotoWriteRoutes(app, deps);
  registerPatientCreateRoutes(app, deps);
  registerEncounterDraftRoutes(app, deps);
  registerEncounterReviewRoutes(app, deps);
  registerLiveEncounterDraftRoutes(app, deps);
  registerLiveMetadataRoutes(app, deps);
  registerLiveAppointmentRoutes(app, deps);
  registerLiveFinanceRoutes(app, deps);
  registerLiveStockRoutes(app, deps);
  registerLivePhotoRoutes(app, deps);
}
