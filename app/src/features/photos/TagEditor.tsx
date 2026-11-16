import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../design/components";
import type { Photo } from "./model";

export interface PhotoTag {
  readonly category: Photo["category"];
  readonly bodyArea: string;
  readonly viewpoint: string;
}

const CATEGORIES: readonly Photo["category"][] = [
  "before",
  "after",
  "profile",
  "clinical",
];
const AREAS = ["face", "scalp", "body"] as const;
const VIEWS = ["front", "left", "right"] as const;

function optionLabel(
  value: string,
  known: readonly string[],
  t: (key: string) => string,
  prefix: string,
): string {
  if (known.includes(value)) return t(`${prefix}.${value}`);
  if (prefix !== "photos.area" && value === "synthetic-frame-v1")
    return t("photos.syntheticView");
  return value;
}

/**
 * Designed body-area tagging edit. The proxy photo read is read-only, so a tag
 * change is a client-side sample tag: it is labelled as not saved and no
 * request is made. The original file is never touched.
 */
export function TagEditor({
  photo,
  tag,
  onSave,
}: {
  photo: Photo;
  tag: PhotoTag;
  onSave: (tag: PhotoTag) => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<PhotoTag["category"]>(tag.category);
  const [bodyArea, setBodyArea] = useState(tag.bodyArea);
  const [viewpoint, setViewpoint] = useState(tag.viewpoint);

  if (!open)
    return (
      <Button size="sm" onClick={() => setOpen(true)}>
        {t("photos.tagEdit")}
      </Button>
    );

  return (
    <div className="photos-tag-editor">
      <div className="photos-field">
        <label htmlFor={`${ids}-category`}>{t("field.photoCategory")}</label>
        <select
          id={`${ids}-category`}
          value={category}
          onChange={(event) =>
            setCategory(event.target.value as PhotoTag["category"])
          }
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {t(`photos.category.${value}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="photos-field">
        <label htmlFor={`${ids}-area`}>{t("field.photoArea")}</label>
        <select
          id={`${ids}-area`}
          value={bodyArea}
          onChange={(event) => setBodyArea(event.target.value)}
        >
          {[...new Set([...AREAS, bodyArea])].map((value) => (
            <option key={value} value={value}>
              {optionLabel(value, AREAS, t, "photos.area")}
            </option>
          ))}
        </select>
      </div>
      <div className="photos-field">
        <label htmlFor={`${ids}-view`}>{t("field.photoView")}</label>
        <select
          id={`${ids}-view`}
          value={viewpoint}
          onChange={(event) => setViewpoint(event.target.value)}
        >
          {[...new Set([...VIEWS, viewpoint])].map((value) => (
            <option key={value} value={value}>
              {optionLabel(value, VIEWS, t, "photos.view")}
            </option>
          ))}
        </select>
      </div>
      <div className="photos-tag-actions">
        <Button
          size="sm"
          variant="primary"
          onClick={() => {
            onSave({ category, bodyArea, viewpoint });
            setOpen(false);
          }}
        >
          {t("photos.tagSave")}
        </Button>
        <Button size="sm" onClick={() => setOpen(false)}>
          {t("action.cancel")}
        </Button>
      </div>
    </div>
  );
}
