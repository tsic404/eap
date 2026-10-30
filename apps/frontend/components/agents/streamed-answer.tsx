"use client";

import { useEffect, useRef, useState } from "react";

/** Reveal tick: one character per frame budget, so the answer grows visibly. */
const STEP_MS = 16;

export interface StreamedAnswerProps {
  /** Full text received so far; grows as the stream appends deltas. */
  text: string;
  /** Whether the stream is still open (blinking caret). */
  streaming: boolean;
}

/**
 * Typewriter reveal of a debug answer.
 *
 * The reveal is append-only: every rendered state is a prefix of the final
 * text, which is what makes "character by character" observable rather than a
 * single swap from empty to complete (TC-agent-04). A model that answers in one
 * burst therefore still types out; a stream that closes keeps revealing to the
 * end instead of snapping.
 *
 * Plain text, not Markdown: a partially received `**` or fence would render as
 * literal characters and break the prefix property the reveal guarantees.
 */
export function StreamedAnswer({ text, streaming }: StreamedAnswerProps) {
  const [visibleChars, setVisibleChars] = useState(0);
  const visibleRef = useRef(0);

  useEffect(() => {
    let frame = 0;
    let last = performance.now();

    const step = (now: number) => {
      if (now - last >= STEP_MS && visibleRef.current < text.length) {
        last = now;
        visibleRef.current += 1;
        setVisibleChars(visibleRef.current);
      }
      if (visibleRef.current < text.length) frame = requestAnimationFrame(step);
    };

    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [text]);

  return (
    <p
      data-role="assistant-message"
      className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground"
    >
      {text.slice(0, visibleChars)}
      {streaming && <span className="streaming-caret" aria-hidden="true" />}
    </p>
  );
}
