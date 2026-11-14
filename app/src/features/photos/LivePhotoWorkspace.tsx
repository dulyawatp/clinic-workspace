import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { assertLegacyRoute } from "../../transport/native/legacy";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import {
  LIVE_PHOTO_PAGE_SIZE,
  type LivePhoto,
  readLivePhotos,
} from "./live-photos";
import { PhotoSave } from "./PhotoSave";
import "./photos.css";

type Patient = {
  workspace_patient_id: string;
  native_patient_id: string;
  display_name: string;
};

async function readPatients(signal: AbortSignal): Promise<Patient[]> {
  assertLegacyRoute();
  const response = await fetch("/api/live/synthetic-patients", {
    credentials: "same-origin",
    cache: "no-store",
    headers: { accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("Live patients unavailable");
  const body: unknown = await response.json();
  if (
    !body ||
    typeof body !== "object" ||
    !("items" in body) ||
    !Array.isArray(body.items)
  )
    throw new Error("Invalid live patient list");
  const keys = new Set<string>();
  for (const item of body.items) {
    if (
      !item ||
      typeof item !== "object" ||
      ![
        item.workspace_patient_id,
        item.native_patient_id,
        item.display_name,
      ].every(
        (value) => typeof value === "string" && value.trim().length > 0,
      ) ||
      keys.has(item.workspace_patient_id)
    )
      throw new Error("Invalid live patient mapping");
    keys.add(item.workspace_patient_id);
  }
  return body.items as Patient[];
}

export function LivePhotoWorkspace() {
  const { t } = useTranslation();
  const { id } = useParams();
  const { status, session, authGeneration } = useSession();
  const allowed =
    status === "authenticated" &&
    session?.mode === "live" &&
    session.user.roles.some((role) => role === "DOC" || role === "NUR");
  const canWrite = allowed && session.user.roles.includes("DOC");
  const queryClient = useQueryClient();
  const patients = useQuery({
    queryKey: ["live-synthetic-patients", authGeneration],
    queryFn: ({ signal }) => readPatients(signal),
    enabled: allowed,
    retry: false,
  });
  const ready = allowed && patients.isFetchedAfterMount && !patients.isFetching;
  const selected =
    ready && !patients.isError
      ? patients.data?.find((patient) => patient.workspace_patient_id === id)
      : undefined;
  return (
    <>
      <PageHeader title={t("livePhotos.title")} />
      <div className="shell-page-body">
        <div className="photos-workspace">
          <div className="photos-caveat">
            <strong>{t("livePhotos.title")}</strong>
            <p>{t("livePhotos.description")}</p>
          </div>
          {status === "authenticated" && !allowed ? (
            <StatePanel kind="error" title={t("livePhotos.unavailable")} />
          ) : !ready ? (
            <StatePanel kind="loading" title={t("common.loading")} />
          ) : patients.isError ? (
            <StatePanel kind="error" title={t("livePhotos.unavailable")} />
          ) : id && !selected ? (
            <StatePanel kind="error" title={t("livePhotos.notFound")} />
          ) : selected ? (
            <>
              <div className="photos-heading">
                <div>
                  <p className="photos-eyebrow">{t("livePhotos.patient")}</p>
                  <h2>{selected.display_name}</h2>
                </div>
                <Link
                  className="ui-button ui-button--secondary"
                  to={ROUTES.livePhotos}
                >
                  {t("livePhotos.back")}
                </Link>
              </div>
              {canWrite && (
                <PhotoSave
                  key={`save:${selected.workspace_patient_id}`}
                  syntheticPatient={selected.workspace_patient_id}
                  onSaved={() => {
                    void queryClient.invalidateQueries({
                      queryKey: [
                        "live-photos",
                        authGeneration,
                        selected.workspace_patient_id,
                      ],
                    });
                  }}
                />
              )}
              <LiveGallery
                key={`gallery:${selected.workspace_patient_id}`}
                patient={selected}
                authGeneration={authGeneration}
              />
            </>
          ) : (
            <section className="photos-card">
              <h2>{t("livePhotos.choose")}</h2>
              {patients.data?.length ? (
                <ul className="photos-live-list">
                  {patients.data.map((patient) => (
                    <li key={patient.workspace_patient_id}>
                      <Link to={ROUTES.livePhoto(patient.workspace_patient_id)}>
                        {patient.display_name}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>{t("livePhotos.empty")}</p>
              )}
            </section>
          )}
        </div>
      </div>
    </>
  );
}

function LiveGallery({
  patient,
  authGeneration,
}: {
  patient: Patient;
  authGeneration: number;
}) {
  const { t } = useTranslation();
  const [offset, setOffset] = useState(0);
  const photos = useQuery({
    queryKey: [
      "live-photos",
      authGeneration,
      patient.workspace_patient_id,
      offset,
    ],
    queryFn: ({ signal }) =>
      readLivePhotos(
        patient.workspace_patient_id,
        patient.native_patient_id,
        offset,
        signal,
      ),
    retry: false,
  });
  return (
    <section className="photos-card" aria-labelledby="live-gallery-title">
      <h3 id="live-gallery-title">{t("livePhotos.gallery")}</h3>
      {photos.isPending || photos.isFetching || !photos.isFetchedAfterMount ? (
        <p role="status">{t("common.loading")}</p>
      ) : photos.isError ? (
        <p role="alert">{t("livePhotos.photosUnavailable")}</p>
      ) : photos.data.length === 0 ? (
        <p>{t("livePhotos.noPhotos")}</p>
      ) : (
        <div className="photos-grid">
          {photos.data.map((photo) => (
            <LivePhotoTile key={photo.photo_id} photo={photo} />
          ))}
        </div>
      )}
      <div className="photos-live-pagination">
        <button
          className="ui-button ui-button--secondary"
          type="button"
          disabled={offset === 0 || photos.isFetching}
          onClick={() => setOffset(Math.max(0, offset - LIVE_PHOTO_PAGE_SIZE))}
        >
          {t("livePhotos.previous")}
        </button>
        <button
          className="ui-button ui-button--secondary"
          type="button"
          disabled={
            photos.isFetching ||
            !photos.data ||
            photos.data.length < LIVE_PHOTO_PAGE_SIZE
          }
          onClick={() => setOffset(offset + LIVE_PHOTO_PAGE_SIZE)}
        >
          {t("livePhotos.next")}
        </button>
      </div>
    </section>
  );
}

function LivePhotoTile({ photo }: { photo: LivePhoto }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  return (
    <figure className="photos-tile">
      <div className="photos-image">
        {failed ? (
          <div className="photos-image-error" role="alert">
            {t("photos.fileUnavailable")}
          </div>
        ) : (
          <img
            src={photo.content_url}
            alt={t("livePhotos.imageAlt", { id: photo.photo_id })}
            onError={() => setFailed(true)}
          />
        )}
      </div>
      <figcaption>
        <strong>{photo.photo_id}</strong>
        <span>
          {photo.width} × {photo.height} px
        </span>
        <span>{photo.created_at}</span>
      </figcaption>
    </figure>
  );
}
