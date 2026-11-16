import {
  annotationVersions,
  type AnnotationVersion,
  photoDrafts,
  type PhotoDraft,
  photoFrames,
  type PhotoFrame,
} from "../../../fixtures/data-clinical";

/**
 * Patient-scoped view of the authored photo/annotation fixtures. The engine
 * never invents a frame or a layer: a patient without authored samples gets the
 * designed empty state instead of another patient's photograph.
 */
export function sampleFramesFor(patientId: string): readonly PhotoFrame[] {
  return photoFrames.filter((frame) => frame.patientId === patientId);
}

export function versionsFor(photoId: string): readonly AnnotationVersion[] {
  return annotationVersions
    .filter((version) => version.photoId === photoId)
    .sort((a, b) => a.versionNumber - b.versionNumber);
}

export function latestVersion(photoId: string): AnnotationVersion | undefined {
  return versionsFor(photoId).at(-1);
}

export function draftsFor(photoId: string): readonly PhotoDraft[] {
  return photoDrafts.filter((draft) => draft.photoId === photoId);
}
