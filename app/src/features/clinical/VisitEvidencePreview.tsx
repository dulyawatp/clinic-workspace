import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Drawer } from "../../design/components";
import sampleProfile from "./sample-profile.svg";
import type { PhotoSide } from "./visit-preview";

export function SamplePhotos({ sides }: { sides: readonly PhotoSide[] }) {
  const { t } = useTranslation();
  const [comparing, setComparing] = useState(false);
  const tiles = (
    <div className="visit-preview-photo-pair">
      {(["before", "after"] as const).map((side) => (
        <div key={side} className="visit-preview-photo-tile">
          <div className="visit-preview-photo-art" data-sample-frame={sides.includes(side) ? side : undefined} aria-hidden="true">
            {sides.includes(side) && <img src={sampleProfile} width={240} height={150} alt="" loading="lazy" />}
          </div>
          <strong>{t(`photos.${side}Select`)}</strong>
          {!sides.includes(side) && <small>{t("clinical.notRecorded")}</small>}
        </div>
      ))}
    </div>
  );
  return (
    <div className="visit-preview-stack">
      {tiles}
      {sides.includes("before") && sides.includes("after") && (
        <Button variant="secondary" onClick={() => setComparing(true)}>
          {t("workspace.compare")}
        </Button>
      )}
      {comparing && (
        <Drawer title={t("workspace.compare")} onClose={() => setComparing(false)}>
          <div className="visit-preview-stack">
            <p>{t("workspace.sampleImages")}</p>
            {tiles}
          </div>
        </Drawer>
      )}
    </div>
  );
}

export function SampleLabFiles({ files }: { files: readonly string[] }) {
  const { t } = useTranslation();
  const [opened, setOpened] = useState<string | null>(null);
  return (
    <>
      {files.length > 0 ? (
        <ul className="visit-preview-files">
          {files.map((file) => (
            <li key={file}>
              <Button variant="secondary" onClick={() => setOpened(file)}>{file}</Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="pc-note">{t("workspace.noReports")}</p>
      )}
      {opened && (
        <Drawer title={opened} onClose={() => setOpened(null)}>
          <p>{t("workspace.sampleReport")}</p>
        </Drawer>
      )}
    </>
  );
}
