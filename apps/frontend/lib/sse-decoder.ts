/**
 * Incremental SSE decoder for the platform stream envelope
 * (``event: <name>`` + ``data: <json>`` frames separated by a blank line).
 *
 * Raw network chunks are buffered here so a frame split across reads still
 * parses as one event. A ``data:`` line that is not valid JSON is skipped
 * without aborting the stream (architecture doc §30.3).
 */

export interface ParsedSseEvent {
  event: string;
  data: Record<string, unknown>;
}

export class SseDecoder {
  private buffer = "";
  private eventName = "";

  /** Feed one raw text chunk; returns every complete event parsed so far. */
  push(chunk: string): ParsedSseEvent[] {
    this.buffer += chunk;
    const lines = this.buffer.split("\n");
    // The final element is an incomplete line (or ""), kept for the next push.
    this.buffer = lines.pop() ?? "";

    const events: ParsedSseEvent[] = [];
    for (const rawLine of lines) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;

      if (line.startsWith("event:")) {
        this.eventName = line.slice("event:".length).trim();
        continue;
      }
      if (!line.startsWith("data:")) continue;

      const payload = line.slice("data:".length).trim();
      if (!payload) continue;

      let data: Record<string, unknown>;
      try {
        data = JSON.parse(payload) as Record<string, unknown>;
      } catch {
        // A truncated/garbled chunk must not kill the stream — drop it. Reset
        // the pending event name too, so a following bare `data:` line cannot
        // inherit the malformed frame's name.
        this.eventName = "";
        continue;
      }

      const event = this.eventName;
      // Reset so a following `data:` line without its own event never inherits
      // a stale name.
      this.eventName = "";
      events.push({ event, data });
    }

    return events;
  }
}
