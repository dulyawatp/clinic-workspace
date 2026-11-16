import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import * as Slider from "react-compare-slider/components";
import { useReactCompareSlider } from "react-compare-slider/hooks";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { useReadQuery } from "../../transport/read/query";
import { PageHeader } from "../../ui/PageHeader";
import { StatePanel } from "../../ui/StatePanel";
import { useCapabilities } from "../shell/CapabilityProvider";
import { AnnotationWorkspace } from "./annotate/AnnotationWorkspace";
import { sampleFramesFor } from "./annotate/sample-frames";
import { ConsentCapture } from "./ConsentCapture";
import { compatible, type Photo, readPatientPhotos } from "./model";
import { PhotoIntake } from "./PhotoIntake";
import { PhotoSave } from "./PhotoSave";
import { type PhotoTag, TagEditor } from "./TagEditor";
import "./photos.css";

type Identity = { patientId: string; patientName: string; fictional: boolean };

function PhotoGate({ compare }: { compare: boolean }) {
  const { id } = useParams();
  const { t } = useTranslation();
  const { status } = useSession();
  const capability = useCapabilities().get("read.photo");
  if (status !== "authenticated")
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (capability === "denied" || capability === "blocked_unverified")
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (capability === "error_upstream")
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  if (!id) return <StatePanel kind="error" title={t("photos.invalid")} />;
  return <PhotoWorkspace key={id} patientId={id} compare={compare} />;
}

function PhotoWorkspace({
  patientId,
  compare,
}: {
  patientId: string;
  compare: boolean;
}) {
  const { t } = useTranslation();
  const { session, authGeneration } = useSession();
  /**
   * The synthetic lab-photo workspace is a live-mode clinician tool and needs a
   * patient context, which this page carries: that is why it is reached from
   * PAT-04/05 instead of a global navigation entry.
   */
  const liveLabPhotos =
    session?.mode === "live" &&
    session.user.roles.some((role) => role === "DOC" || role === "NUR");
  const photos = useQuery<Photo[], AppError>({
    queryKey: ["patient-photos", patientId, authGeneration],
    queryFn: ({ signal }) => readPatientPhotos(patientId, signal),
    retry: false,
  });
  const identity = useReadQuery<Identity>("clinical-visits", {
    filters: { patientId },
    limit: 20,
  });
  if (
    photos.error?.code === "FORBIDDEN" ||
    identity.error?.code === "FORBIDDEN"
  )
    return (
      <StatePanel kind="permission-denied" title={t("state.denied.title")} />
    );
  if (photos.isError || identity.isError)
    return <StatePanel kind="error" title={t("errors.UPSTREAM_UNAVAILABLE")} />;
  if (
    photos.isPending ||
    photos.isFetching ||
    !photos.isFetchedAfterMount ||
    identity.isPending ||
    identity.isFetching ||
    !identity.isFetchedAfterMount
  )
    return <StatePanel kind="loading" title={t("common.loading")} />;
  if (
    identity.data.items.some(
      (row) =>
        !row ||
        typeof row !== "object" ||
        row.patientId !== patientId ||
        row.fictional !== true ||
        typeof row.patientName !== "string" ||
        !row.patientName.trim(),
    )
  )
    return <StatePanel kind="error" title={t("photos.invalid")} />;
  const name = identity.data.items[0]?.patientName;
  return (
    <div className="photos-workspace">
      <div className="photos-heading">
        <div>
          <p className="photos-eyebrow">{t("photos.fixtureLabel")}</p>
          <h2>{name || t("clinical.patient")}</h2>
          <p>
            {t("clinical.patientId")}: <strong>{patientId}</strong>
          </p>
        </div>
        <Link
          className="ui-button ui-button--secondary"
          to={
            compare ? ROUTES.photos(patientId) : ROUTES.photoCompare(patientId)
          }
        >
          {t(compare ? "photos.back" : "photos.compare")}
        </Link>
        {liveLabPhotos && (
          <Link
            className="ui-button ui-button--secondary"
            to={ROUTES.livePhotosPatient(patientId)}
          >
            {t("livePhotos.title")}
          </Link>
        )}
      </div>
      <div className="photos-caveat">
        <strong>{t("photos.demoTitle")}</strong>
        <p>{t("photos.demoBody")}</p>
      </div>
      {photos.data.length > 0 && <Consent photos={photos.data} />}
      {!compare && <PhotoSave />}
      {photos.data.length === 0 ? (
        <StatePanel kind="empty" title={t("photos.empty")} />
      ) : compare ? (
        <CompareWorkspace photos={photos.data} />
      ) : (
        <Gallery photos={photos.data} patientId={patientId} />
      )}
    </div>
  );
}

function PhotoImage({
  photo,
  onFailure,
}: {
  photo: Photo;
  onFailure?: () => void;
}) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <div className="photos-image-error" role="alert">
        {t("photos.fileUnavailable")}
      </div>
    );
  return (
    <img
      src={photo.url}
      alt={`${t(`photos.category.${photo.category}`)} · ${photo.bodyArea}`}
      onError={() => {
        setFailed(true);
        onFailure?.();
      }}
    />
  );
}

function PhotoMeta({ photo, tag }: { photo: Photo; tag?: PhotoTag }) {
  const { t, i18n } = useTranslation();
  const effective: PhotoTag = tag ?? {
    category: photo.category,
    bodyArea: photo.bodyArea,
    viewpoint: photo.viewpoint ?? "",
  };
  const date = new Intl.DateTimeFormat(
    i18n.language === "th" ? "th-TH" : "en-GB",
    { dateStyle: "medium", timeStyle: "short" },
  ).format(new Date(photo.takenAt));
  const area = ["face", "scalp", "body"].includes(effective.bodyArea)
    ? t(`photos.area.${effective.bodyArea}`)
    : effective.bodyArea;
  const view = ["front", "left", "right"].includes(effective.viewpoint)
    ? t(`photos.view.${effective.viewpoint}`)
    : photo.viewpoint === "synthetic-frame-v1"
      ? t("photos.syntheticView")
      : t("photos.unknownView");
  return (
    <>
      <strong>{t(`photos.category.${effective.category}`)}</strong>
      <span>{photo.id}</span>
      <span>
        {t("photos.area")}: {area}
      </span>
      <span>
        {t("photos.view")}: {view}
      </span>
      <time dateTime={photo.takenAt}>{date}</time>
    </>
  );
}

function Consent({ photos }: { photos: Photo[] }) {
  const { t } = useTranslation();
  const hasMissing = photos.some((photo) => photo.consentStatus === "missing");
  const hasUnknown = photos.some((photo) => photo.consentStatus === "unknown");
  return (
    <section className="photos-consent" aria-label={t("photos.consent")}>
      <strong>{t("photos.consent")}</strong>
      <p>
        {t(
          hasMissing
            ? "photos.consentMissing"
            : hasUnknown
              ? "photos.consentUnknown"
              : "photos.consentRecorded",
        )}
      </p>
      {hasMissing && <p>{t("photos.consentRemedy")}</p>}
    </section>
  );
}

function Gallery({
  photos,
  patientId,
}: {
  photos: Photo[];
  patientId: string;
}) {
  const { t } = useTranslation();
  const [tags, setTags] = useState<Record<string, PhotoTag>>({});
  const [consentOpen, setConsentOpen] = useState(false);
  const [tagSaved, setTagSaved] = useState(false);
  return (
    <>
      <PhotoIntake photos={photos} onRecordConsent={() => setConsentOpen(true)} />
      <ConsentCapture
        patientId={patientId}
        open={consentOpen}
        onClose={() => setConsentOpen(false)}
      />
      {tagSaved && (
        <p className="photos-consent-state" role="status">
          {t("photos.tagLocal")}
        </p>
      )}
      <section className="photos-card">
        <div className="photos-section-head">
          <div>
            <p className="photos-eyebrow">{t("photos.readOnly")}</p>
            <h3>{t("photos.originals")}</h3>
          </div>
          <span>{photos.length}</span>
        </div>
        <div className="photos-grid">
          {photos.map((photo) => {
            const tag = tags[photo.id] ?? {
              category: photo.category,
              bodyArea: photo.bodyArea,
              viewpoint: photo.viewpoint ?? "",
            };
            return (
              <figure className="photos-tile" key={photo.id}>
                <div className="photos-image">
                  <PhotoImage photo={photo} />
                </div>
                <figcaption>
                  <PhotoMeta photo={photo} tag={tag} />
                  <span
                    className={`photos-badge photos-badge--${photo.consentStatus}`}
                  >
                    {t(`photos.consent.${photo.consentStatus}`)}
                  </span>
                  <TagEditor
                    photo={photo}
                    tag={tag}
                    onSave={(next) => {
                      setTags((current) => ({ ...current, [photo.id]: next }));
                      setTagSaved(true);
                    }}
                  />
                </figcaption>
              </figure>
            );
          })}
        </div>
      </section>
    </>
  );
}

function CompareWorkspace({ photos }: { photos: Photo[] }) {
  const { t, i18n } = useTranslation();
  const before = photos.filter((photo) => photo.category === "before");
  const after = photos.filter((photo) => photo.category === "after");
  const [beforeId, setBefore] = useState(before[0]?.id ?? "");
  const [afterId, setAfter] = useState(after[0]?.id ?? "");
  const left = before.find((photo) => photo.id === beforeId);
  const right = after.find((photo) => photo.id === afterId);
  const label = (photo: Photo) =>
    `${photo.id} · ${photo.bodyArea} · ${new Date(photo.takenAt).toLocaleString(i18n.language === "th" ? "th-TH" : "en-GB")}`;
  return (
    <section className="photos-card">
      <div className="photos-section-head">
        <div>
          <p className="photos-eyebrow">{t("photos.readOnly")}</p>
          <h3>{t("photos.compare")}</h3>
        </div>
      </div>
      <p>{t("photos.compareNote")}</p>
      <div className="photos-selectors">
        <label>
          {t("photos.beforeSelect")}
          <select value={beforeId} onChange={(e) => setBefore(e.target.value)}>
            <option value="">{t("photos.choose")}</option>
            {before.map((photo) => (
              <option key={photo.id} value={photo.id}>
                {label(photo)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("photos.afterSelect")}
          <select value={afterId} onChange={(e) => setAfter(e.target.value)}>
            <option value="">{t("photos.choose")}</option>
            {after.map((photo) => (
              <option key={photo.id} value={photo.id}>
                {label(photo)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {left && right && compatible(left, right) ? (
        <>
          <Comparison
            key={`${left.id}:${right.id}`}
            before={left}
            after={right}
          />
          {sampleFramesFor(left.patientId).length === 0 && (
            <AnnotationWorkspace
              key={`annotate:${left.id}:${right.id}`}
              patientId={left.patientId}
              photos={[left, right]}
            />
          )}
        </>
      ) : (
        <StatePanel
          kind="empty"
          title={t("photos.incompatible")}
          detail={t("photos.incompatibleBody")}
        />
      )}
    </section>
  );
}

function Comparison({ before, after }: { before: Photo; after: Photo }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const [position, setPosition] = useState(50);
  const slider = useReactCompareSlider({ onPositionChange: setPosition });
  return (
    <div data-testid="compare-pair">
      <h4>{t("photos.sideBySide")}</h4>
      <div className="photos-side-by-side">
        {[before, after].map((photo) => (
          <figure className="photos-tile" key={photo.id}>
            <div className="photos-image">
              <PhotoImage photo={photo} onFailure={() => setFailed(true)} />
            </div>
            <figcaption>
              <PhotoMeta photo={photo} />
            </figcaption>
          </figure>
        ))}
      </div>
      {failed ? (
        <StatePanel kind="error" title={t("photos.fileUnavailable")} />
      ) : (
        <>
          <h4>{t("photos.slider")}</h4>
          <Slider.Provider {...slider}>
            <Slider.Root className="photos-slider">
              <Slider.Item item="itemOne">
                <PhotoImage photo={before} onFailure={() => setFailed(true)} />
              </Slider.Item>
              <Slider.Item item="itemTwo">
                <PhotoImage photo={after} onFailure={() => setFailed(true)} />
              </Slider.Item>
              <Slider.HandleRoot aria-label={t("photos.reveal")}>
                <Slider.Handle />
              </Slider.HandleRoot>
            </Slider.Root>
          </Slider.Provider>
          <label className="photos-range">
            {t("photos.reveal")}{" "}
            <span aria-hidden="true">{Math.round(position)}%</span>
            <input
              type="range"
              min="0"
              max="100"
              value={position}
              onChange={(event) => {
                const value = Number(event.target.value);
                slider.setPosition(value);
                setPosition(value);
              }}
            />
          </label>
        </>
      )}
    </div>
  );
}

function PhotoPage({ compare }: { compare: boolean }) {
  const { t } = useTranslation();
  const { id } = useParams();
  return (
    <>
      <PageHeader title={t(compare ? "photos.compare" : "photos.gallery")} />
      <div className="shell-page-body">
        <PhotoGate compare={compare} />
        {/* PAT-05 annotation is fully client-side over the authored sample
            frames, so it renders even when the proxy photo read is empty. */}
        {compare &&
          id !== undefined &&
          sampleFramesFor(id).length > 0 && (
            <AnnotationWorkspace patientId={id} />
          )}
      </div>
    </>
  );
}
export function PhotoGalleryPage() {
  return <PhotoPage compare={false} />;
}
export function PhotoComparePage() {
  return <PhotoPage compare />;
}
