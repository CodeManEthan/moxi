// A text/event-stream reader, fed the decoded text as it arrives. The push stream is read
// through fetch, since EventSource can't send the key header, so the frames are parsed
// here: `event:` and `data:` lines, a blank line ending each event, `:` comments ignored.

/** One event: its name (`message` when the frame names none) and its data lines joined. */
export interface SseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private sawData = false;

  /** The events `chunk` completes, in order. A line split across chunks is kept. */
  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const done: SseEvent[] = [];
    let start = 0;
    for (;;) {
      const lf = this.buffer.indexOf("\n", start);
      const cr = this.buffer.indexOf("\r", start);
      let end: number;
      let next: number;
      if (cr !== -1 && (lf === -1 || cr < lf)) {
        // A CR at the very end may be the first half of a CRLF: wait for the next chunk.
        if (cr === this.buffer.length - 1) break;
        end = cr;
        next = this.buffer[cr + 1] === "\n" ? cr + 2 : cr + 1;
      } else if (lf !== -1) {
        end = lf;
        next = lf + 1;
      } else {
        break;
      }
      const ended = this.line(this.buffer.slice(start, end));
      if (ended !== null) done.push(ended);
      start = next;
    }
    this.buffer = this.buffer.slice(start);
    return done;
  }

  private line(line: string): SseEvent | null {
    if (line === "") {
      const event = this.sawData ? { event: this.event || "message", data: this.data.join("\n") } : null;
      this.event = "";
      this.data = [];
      this.sawData = false;
      return event;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      this.event = value;
    } else if (field === "data") {
      this.data.push(value);
      this.sawData = true;
    }
    return null;
  }
}
