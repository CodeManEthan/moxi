// The line's colours as the page draws them. The render prints a few SGR codes: 0 (reset),
// 1 (bold), 2 (dim) and 31-37 (the colours). Each becomes a style of a run of text; every
// other escape, and any other control character, is dropped, so nothing the line prints
// reaches the page as anything but text.

/** One run of text in one style. `color` is the SGR code, 31 to 37, or null. */
export interface Run {
  text: string;
  bold: boolean;
  dim: boolean;
  color: number | null;
}

const ESC = "\u001b";
const BEL = "\u0007";

interface Style {
  bold: boolean;
  dim: boolean;
  color: number | null;
}

function plain(): Style {
  return { bold: false, dim: false, color: null };
}

/** Applies one SGR sequence's parameters to `style`. An empty parameter is 0; a code the
 * page doesn't draw is passed over. */
function apply(style: Style, params: string): Style {
  let next = { ...style };
  for (const part of params.split(";")) {
    if (!/^\d*$/.test(part)) continue;
    const code = part === "" ? 0 : Number(part);
    if (code === 0) next = plain();
    else if (code === 1) next.bold = true;
    else if (code === 2) next.dim = true;
    else if (code >= 31 && code <= 37) next.color = code;
  }
  return next;
}

/** Where the escape starting at `at` (an ESC) ends, and the SGR parameters when it is one:
 * a CSI ending in `m`. An OSC runs to BEL or ESC `\`; any other escape is ESC and one
 * character. */
function escape(line: string, at: number): { end: number; sgr: string | null } {
  const kind = line[at + 1];
  if (kind === "[") {
    let i = at + 2;
    while (i < line.length) {
      const code = line.charCodeAt(i);
      if (code >= 0x40 && code <= 0x7e) {
        return { end: i + 1, sgr: line[i] === "m" ? line.slice(at + 2, i) : null };
      }
      i += 1;
    }
    return { end: line.length, sgr: null };
  }
  if (kind === "]") {
    let i = at + 2;
    while (i < line.length) {
      if (line[i] === BEL) return { end: i + 1, sgr: null };
      if (line[i] === ESC && line[i + 1] === "\\") return { end: i + 2, sgr: null };
      i += 1;
    }
    return { end: line.length, sgr: null };
  }
  return { end: Math.min(at + 2, line.length), sgr: null };
}

function control(code: number): boolean {
  return (code < 0x20 && code !== 0x09) || code === 0x7f;
}

/** The runs of one printed row, in order, with no empty run. */
export function runs(line: string): Run[] {
  const out: Run[] = [];
  let style = plain();
  let text = "";
  const flush = () => {
    if (text !== "") out.push({ text, ...style });
    text = "";
  };
  let i = 0;
  while (i < line.length) {
    const ch = line[i];
    if (ch === ESC) {
      const found = escape(line, i);
      if (found.sgr !== null) {
        const next = apply(style, found.sgr);
        if (next.bold !== style.bold || next.dim !== style.dim || next.color !== style.color) {
          flush();
          style = next;
        }
      }
      i = found.end;
      continue;
    }
    if (!control(line.charCodeAt(i))) text += ch;
    i += 1;
  }
  flush();
  return out;
}

/** The class names a run is drawn with: `b` bold, `d` dim, `c31` to `c37` a colour. */
export function classes(run: Run): string {
  const names: string[] = [];
  if (run.bold) names.push("b");
  if (run.dim) names.push("d");
  if (run.color !== null) names.push(`c${run.color}`);
  return names.join(" ");
}

/** A row's text with every escape dropped. */
export function text(line: string): string {
  return runs(line).map((run) => run.text).join("");
}
