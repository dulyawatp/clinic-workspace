import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../app/SessionProvider";
import { Button } from "../../ui/Button";
import type { EncounterReadError } from "../../transport/native/encounter-read";
import { listHistoryNotes } from "../../transport/native/history-notes";
import { FormattedNotePreview } from "../clinical/LinkedFormattedNote";
import { noteContent, parseFormattedNote } from "../clinical/linked-note-format";

type Context = { encounterId: string; patientId: string };

function HistoryNotes({ encounterId, patientId }: Context) {
  const { t } = useTranslation();
  const { authGeneration } = useSession();
  const query = useInfiniteQuery({
    queryKey: ["native-history-notes", authGeneration, encounterId, patientId],
    queryFn: ({ pageParam, signal }) => listHistoryNotes(encounterId, patientId, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: ({ nextOffset }) => nextOffset,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const notes = query.data?.pages.flatMap(({ items }) => items) ?? [];
  const code = (query.error as EncounterReadError | null)?.code;
  if (query.isError || new Set(notes.map(({ id }) => id)).size !== notes.length) {
    return <p className="pc-note" role="alert">{t(code === "FORBIDDEN" || code === "AUTH_EXPIRED"
      ? "state.denied.title" : "workspace.savedNoteUnavailable")}</p>;
  }
  if (query.isPending || !query.isFetchedAfterMount) return <p role="status">{t("common.loading")}</p>;
  if (!notes.length) return <p className="pc-note">{t("linkedEncounter.noNotes")}</p>;
  return (
    <div className="visit-preview-stack">
      {notes.map(({ id, text }) => {
        const content = noteContent(text);
        const lines = content.rich ? parseFormattedNote(text) : null;
        return <article className="visit-preview-stack visit-linked-note" key={id}>
          <h5>{id}</h5>
          {lines ? <FormattedNotePreview lines={lines} /> :
            <p className="visit-history-note">{content.text || t("linkedEncounter.noNoteText")}</p>}
        </article>;
      })}
      {query.hasNextPage && <div><Button variant="secondary" disabled={query.isFetching}
        onClick={() => void query.fetchNextPage()}>{t("linkedEncounter.moreNotes")}</Button></div>}
    </div>
  );
}

export function HistoryNoteDisclosure(props: Context) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <details className="visit-preview-details" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t("workspace.readNote")}</summary>
      {open && <HistoryNotes {...props} />}
    </details>
  );
}
