import { describe, expect, it } from "vitest";

import { SseDecoder } from "./sse-decoder";

describe("SseDecoder", () => {
  it("parses an event + data frame", () => {
    const decoder = new SseDecoder();
    expect(decoder.push('event: message\ndata: {"content":"你"}\n\n')).toEqual([
      { event: "message", data: { content: "你" } },
    ]);
  });

  it("buffers a frame split across chunks", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: mes")).toEqual([]);
    expect(decoder.push('sage\ndata: {"content":"你"}\n\n')).toEqual([
      { event: "message", data: { content: "你" } },
    ]);
  });

  it("skips a malformed data line without aborting the stream", () => {
    const decoder = new SseDecoder();
    const events = decoder.push(
      'event: message\ndata: {"broken"\n\nevent: message\ndata: {"content":"ok"}\n\n',
    );
    expect(events).toEqual([{ event: "message", data: { content: "ok" } }]);
  });

  it("does not inherit a stale event name on a bare data line", () => {
    const decoder = new SseDecoder();
    const events = decoder.push(
      'event: message\ndata: {"content":"first"}\n\ndata: {"content":"orphan"}\n\n',
    );
    expect(events).toEqual([
      { event: "message", data: { content: "first" } },
      { event: "", data: { content: "orphan" } },
    ]);
  });

  it("resets the event name when a data line fails to parse", () => {
    const decoder = new SseDecoder();
    const events = decoder.push(
      'event: message\ndata: {"broken"\n\ndata: {"content":"orphan"}\n\n',
    );
    // The malformed `message` frame is dropped; the bare `data:` line must not
    // be dispatched under the dropped frame's event name.
    expect(events).toEqual([{ event: "", data: { content: "orphan" } }]);
  });

  it("tolerates CRLF line endings", () => {
    const decoder = new SseDecoder();
    expect(decoder.push('event: message\r\ndata: {"content":"你"}\r\n\r\n')).toEqual([
      { event: "message", data: { content: "你" } },
    ]);
  });

  it("ignores comment lines and keeps ping frames", () => {
    const decoder = new SseDecoder();
    expect(decoder.push(": keepalive\nevent: ping\ndata: {}\n\n")).toEqual([
      { event: "ping", data: {} },
    ]);
  });
});
