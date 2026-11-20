import { Delta } from "quill";

const marks = { bold: "strong", italic: "em", underline: "u", strike: "s" } as const;
export type NoteMark = keyof typeof marks;
type List = "ordered" | "bullet";
type Heading = 1 | 2 | 3 | 4 | 5 | 6;
type Run = { text: string; marks: NoteMark[] };
export type NoteLine = { kind: "p" | List | Heading; runs: Run[] };
const markNames = Object.keys(marks) as NoteMark[];
const htmlNamespace = "http://www.w3.org/1999/xhtml";
const wrapperClass = "ql-editor read-mode";
const listMarker = '<span class="ql-ui" contenteditable="false"></span>';

export const noteFormats = [...markNames, "header", "list"];
export const noteMarks = markNames;
export const noteMarkTag = (mark: NoteMark) => marks[mark];
export const noteList = (kind: NoteLine["kind"]): kind is List => kind === "ordered" || kind === "bullet";
export const noteBlockTag = (kind: NoteLine["kind"]) =>
  typeof kind === "number" ? `h${kind}` : noteList(kind) ? "li" : kind;

function element(node: Node | undefined, tag?: string): node is HTMLElement {
  return !!node && node instanceof HTMLElement && node.namespaceURI === htmlNamespace &&
    (tag === undefined || node.localName === tag);
}

function addRun(runs: Run[], text: string, active: NoteMark[]) {
  if (!text) return;
  const ordered = markNames.filter((mark) => active.includes(mark));
  const previous = runs.at(-1);
  if (previous && previous.marks.join() === ordered.join()) previous.text += text;
  else runs.push({ text, marks: ordered });
}

function inline(nodes: Node[], runs: Run[], active: NoteMark[] = []): boolean {
  if (nodes.length === 1 && element(nodes[0], "br") && nodes[0].attributes.length === 0) {
    return active.length === 0;
  }
  for (const node of nodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? "";
      if (/[\r\n\t]/.test(text)) return false;
      addRun(runs, text, active);
      continue;
    }
    if (!element(node) || node.attributes.length || !node.hasChildNodes()) return false;
    const mark = markNames.find((name) => marks[name] === node.localName);
    if (!mark || active.includes(mark) || !inline(Array.from(node.childNodes), runs, [...active, mark])) return false;
  }
  return true;
}

/** Reject the whole note on any unknown node, attribute, repaired HTML or lossy block shape. */
export function parseFormattedNote(source: string): NoteLine[] | null {
  const template = document.createElement("template");
  template.innerHTML = source;
  if (source !== template.innerHTML || template.content.childNodes.length !== 1) return null;
  const root = template.content.firstElementChild;
  if (!root || !element(root, "div") || root.getAttribute("class") !== wrapperClass || root.attributes.length !== 1) return null;
  const lines: NoteLine[] = [];
  for (const block of root.childNodes) {
    if (!element(block) || block.attributes.length) return null;
    if (block.localName === "ol" || block.localName === "ul") {
      const kind = block.localName === "ol" ? "ordered" : "bullet";
      if (!block.hasChildNodes() || lines.at(-1)?.kind === kind) return null;
      for (const child of block.childNodes) {
        if (!element(child, "li") || child.attributes.length !== 1 || child.getAttribute("data-list") !== kind) return null;
        const children = Array.from(child.childNodes);
        if (element(children[0], "span") && children[0].outerHTML === listMarker) children.shift();
        const runs: Run[] = [];
        if (!children.length || !inline(children, runs)) return null;
        lines.push({ kind, runs });
      }
    } else {
      const heading = /^h([1-6])$/.exec(block.localName);
      const kind = block.localName === "p" ? "p" : heading ? Number(heading[1]) as Heading : null;
      const runs: Run[] = [];
      if (kind === null || !block.hasChildNodes() || !inline(Array.from(block.childNodes), runs)) return null;
      lines.push({ kind, runs });
    }
  }
  return lines.length ? lines : null;
}

export function noteDelta(lines: NoteLine[]) {
  const delta = new Delta();
  for (const line of lines) {
    for (const run of line.runs) {
      delta.insert(run.text, Object.fromEntries(run.marks.map((mark) => [mark, true])));
    }
    delta.insert("\n", typeof line.kind === "number" ? { header: line.kind } :
      noteList(line.kind) ? { list: line.kind } : undefined);
  }
  return delta;
}

/** Only text and the named marks/line styles are accepted, even from the editor. */
export function noteLines(delta: Delta): NoteLine[] | null {
  const lines: NoteLine[] = [];
  let runs: Run[] = [];
  for (const { insert, attributes = {} } of delta.ops) {
    if (typeof insert !== "string" || /[\r\t]/.test(insert)) return null;
    if (insert === "\n" && ("header" in attributes || "list" in attributes)) {
      if (Object.keys(attributes).length !== 1) return null;
      const { header, list } = attributes;
      const kind = list === "ordered" || list === "bullet" ? list :
        typeof header === "number" && Number.isInteger(header) && header >= 1 && header <= 6 ? header as Heading : null;
      if (kind === null) return null;
      lines.push({ kind, runs });
      runs = [];
      continue;
    }
    const current = Object.keys(attributes) as NoteMark[];
    if (current.some((mark) => !markNames.includes(mark) || attributes[mark] !== true) ||
        (current.length && insert.includes("\n"))) return null;
    const chunks = insert.split("\n");
    chunks.forEach((text, index) => {
      if (index > 0) {
        lines.push({ kind: "p", runs });
        runs = [];
      }
      addRun(runs, text, current);
    });
  }
  return lines.length && !runs.length ? lines : null;
}

export function encodeNoteText(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

export function notePlainText(lines: NoteLine[]) {
  return lines.map(({ runs }) => runs.map(({ text }) => text).join("")).join("\n");
}

export function noteBlocks(lines: NoteLine[]) {
  const blocks: { list: List | null; lines: NoteLine[] }[] = [];
  for (const line of lines) {
    const list = noteList(line.kind) ? line.kind : null;
    const last = blocks.at(-1);
    if (list && last?.list === list) last.lines.push(line);
    else blocks.push({ list, lines: [line] });
  }
  return blocks;
}

/** Do not use Quill HTML export. Emit closed tags/attributes and escaped text only. */
export function serializeFormattedNote(lines: NoteLine[]) {
  const body = noteBlocks(lines).map(({ list, lines: block }) => {
    const content = block.map(({ kind, runs }) => {
      const tag = noteBlockTag(kind);
      const listAttrs = noteList(kind) ? ` data-list="${kind}"` : "";
      const text = runs.map((run) => run.marks.reduceRight(
        (value, mark) => `<${marks[mark]}>${value}</${marks[mark]}>`,
        encodeNoteText(run.text).replaceAll("\u00a0", "&nbsp;"),
      )).join("") || "<br>";
      return `<${tag}${listAttrs}>${list ? listMarker : ""}${text}</${tag}>`;
    }).join("");
    return list === "ordered" ? `<ol>${content}</ol>` : list === "bullet" ? `<ul>${content}</ul>` : content;
  }).join("");
  return `<div class="${wrapperClass}">${body}</div>`;
}
