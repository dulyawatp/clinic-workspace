import { Delta, default as Quill } from "quill";
import "quill/dist/quill.core.css";
import { createElement, Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  noteBlocks,
  noteBlockTag,
  noteDelta,
  noteFormats,
  noteLines,
  noteMarks,
  noteMarkTag,
  notePlainText,
  type NoteLine,
  serializeFormattedNote,
} from "./linked-note-format";

export function FormattedNotePreview({ lines }: { lines: NoteLine[] }) {
  return <div className="visit-linked-formatted">
    {noteBlocks(lines).map(({ list, lines: block }, index) => {
      const content = block.map(({ kind, runs }, i) => createElement(noteBlockTag(kind), { key: i },
        runs.length ? runs.map(({ marks, text }, j) =>
          <Fragment key={j}>{marks.reduceRight<ReactNode>(
            (child, mark) => createElement(noteMarkTag(mark), null, child), text,
          )}</Fragment>) : <br />));
      return list ? createElement(list === "ordered" ? "ol" : "ul", { key: index }, content) :
        <Fragment key={index}>{content}</Fragment>;
    })}
  </div>;
}

export function FormattedNoteEditor({ lines, disabled, onChange }: {
  lines: NoteLine[];
  disabled: boolean;
  onChange: (value: { html: string; text: string } | null) => void;
}) {
  const { t } = useTranslation();
  const label = t("workspace.narrative");
  const host = useRef<HTMLDivElement>(null);
  const quill = useRef<Quill | null>(null);
  const onChangeRef = useRef(onChange);
  const [unsupported, setUnsupported] = useState(false);
  const [invalidChange, setInvalidChange] = useState(false);
  const [selection, setSelection] = useState<Record<string, unknown>>({});
  onChangeRef.current = onChange;

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    // Use a bounded registry and no built-in toolbars/uploader. Neither HTML
    // input nor Quill's HTML export participates in this clinical note path.
    const mount = document.createElement("div");
    container.append(mount);
    const editor = new Quill(mount, { formats: noteFormats, readOnly: true, modules: { toolbar: false, uploader: false } });
    editor.root.setAttribute("role", "textbox");
    editor.root.setAttribute("aria-multiline", "true");
    const blockDrop = (event: Event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    const clipboard = (event: ClipboardEvent) => {
      blockDrop(event);
      const data = event.clipboardData;
      const range = editor.getSelection();
      if (!data || !range) return;
      if (event.type === "paste") {
        if (!editor.isEnabled() || data.files.length) return;
        const text = data.getData("text/plain").replace(/\r\n?/g, "\n").replaceAll("\t", "    ");
        if (!text) return;
        editor.updateContents(new Delta().retain(range.index).delete(range.length).insert(text), "user");
        editor.setSelection(range.index + text.length, 0, "silent");
      } else {
        data.setData("text/plain", editor.getText(range.index, range.length));
        if (event.type === "cut" && editor.isEnabled()) editor.deleteText(range.index, range.length, "user");
      }
    };
    // Capture before Quill's default clipboard can import or export HTML.
    const clipboardEvents = ["paste", "copy", "cut"] as const;
    clipboardEvents.forEach((event) => editor.root.addEventListener(event, clipboard, true));
    editor.root.addEventListener("drop", blockDrop, true);
    editor.root.addEventListener("dragover", blockDrop, true);
    editor.root.addEventListener("dragstart", blockDrop, true);
    const updateSelection = () => {
      const range = editor.getSelection();
      if (range) setSelection(editor.getFormat(range));
    };
    const changed = () => {
      const current = noteLines(editor.getContents());
      setInvalidChange(current === null);
      onChangeRef.current(current ? { html: serializeFormattedNote(current), text: notePlainText(current) } : null);
      updateSelection();
    };
    editor.setContents(noteDelta(lines), "silent");
    editor.history.clear();
    const roundTrip = noteLines(editor.getContents());
    if (!roundTrip || serializeFormattedNote(roundTrip) !== serializeFormattedNote(lines)) {
      setUnsupported(true);
      onChangeRef.current(null);
    } else {
      quill.current = editor;
      editor.on("text-change", changed);
      editor.on("selection-change", updateSelection);
    }
    return () => {
      quill.current = null;
      editor.off("text-change", changed);
      editor.off("selection-change", updateSelection);
      clipboardEvents.forEach((event) => editor.root.removeEventListener(event, clipboard, true));
      editor.root.removeEventListener("drop", blockDrop, true);
      editor.root.removeEventListener("dragover", blockDrop, true);
      editor.root.removeEventListener("dragstart", blockDrop, true);
      mount.remove();
    };
  }, [lines]);

  useEffect(() => {
    const editor = quill.current;
    if (!editor) return;
    editor.enable(!disabled);
    editor.root.setAttribute("aria-label", label);
    editor.root.setAttribute("aria-readonly", String(disabled));
  }, [disabled, label, lines]);

  const toggleFormat = (key: string, value: true | "ordered" | "bullet") => {
    const editor = quill.current;
    if (!editor || disabled) return;
    editor.focus({ preventScroll: true });
    const range = editor.getSelection();
    if (!range) return;
    editor.format(key, editor.getFormat(range)[key] === value ? false : value, "user");
    setSelection(editor.getFormat(range));
  };

  return <div className="visit-linked-formatted-editor">
    <div role="group" aria-label={t("linkedEncounter.noteFormatting")} className="visit-linked-format-toolbar" hidden={unsupported}>
      {noteMarks.map((mark) => <button key={mark} type="button" disabled={disabled}
        onPointerDown={(event) => event.preventDefault()} aria-pressed={selection[mark] === true}
        onClick={() => toggleFormat(mark, true)}>
        {t(`linkedEncounter.format.${mark}`)}
      </button>)}
      {(["ordered", "bullet"] as const).map((list) => <button key={list} type="button" disabled={disabled}
        onPointerDown={(event) => event.preventDefault()} aria-pressed={selection.list === list}
        onClick={() => toggleFormat("list", list)}>
        {t(`linkedEncounter.format.${list}`)}
      </button>)}
    </div>
    <div ref={host} hidden={unsupported} />
    {unsupported && <p role="status">{t("linkedEncounter.formattedNote")}</p>}
    {invalidChange && <p role="alert">{t("linkedEncounter.invalidNoteFormatting")}</p>}
  </div>;
}
