"use client";

import { useEffect, useRef, useState } from "react";

import { MarkdownText } from "./markdown-text";

const CHAR_INTERVAL_MS = 30;

export interface StreamingTextProps {
  /** Full target text; grows as SSE chunks append. */
  text: string;
  /** Whether the stream is still active (blinking cursor). */
  active?: boolean;
}

/**
 * Typewriter reveal of streamed text. A `requestAnimationFrame` loop advances
 * the visible slice one character every ~30ms, re-rendering it as Markdown; an
 * active stream appends a blinking caret. A `replace` (content that does not
 * extend the previous text) is shown in full immediately, rather than replaying
 * a stale typewriter slice over the swapped content.
 */
export function StreamingText({ text, active = true }: StreamingTextProps) {
  const [visibleCount, setVisibleCount] = useState(0);
  const prevTextRef = useRef("");

  useEffect(() => {
    const previous = prevTextRef.current;
    prevTextRef.current = text;

    if (!active) {
      setVisibleCount(text.length);
      return;
    }

    // A replace swaps the content wholesale (e.g. the backend's `replace`
    // event): show it fully instead of revealing from a stale offset.
    if (!text.startsWith(previous)) {
      setVisibleCount(text.length);
      return;
    }

    let last = performance.now();
    let frame = 0;
    const step = (now: number) => {
      if (now - last >= CHAR_INTERVAL_MS) {
        last = now;
        setVisibleCount((count) => (count >= text.length ? count : count + 1));
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [active, text]);

  const visible = text.slice(0, visibleCount);

  return (
    <span className="inline-block whitespace-pre-wrap break-words">
      <MarkdownText text={visible} />
      {active && <span className="streaming-caret" aria-hidden="true" />}
    </span>
  );
}
