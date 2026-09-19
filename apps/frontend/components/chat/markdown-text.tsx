"use client";

import { type ReactNode } from "react";

const BLOCK_OPEN_RE = /^(#{1,6})\s+/;
const UNORDERED_RE = /^[-*]\s+/;
const ORDERED_RE = /^\d+\.\s+/;

function isFence(line: string): boolean {
  return line.startsWith("```");
}

function isBlockStart(line: string): boolean {
  return (
    isFence(line) ||
    BLOCK_OPEN_RE.test(line) ||
    UNORDERED_RE.test(line) ||
    ORDERED_RE.test(line)
  );
}

/** Inline span: `` `code` ``, ``**bold**``, ``*italic*``. Unclosed tokens stay literal. */
function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let buffer = "";
  let index = 0;

  const flush = () => {
    if (buffer) {
      nodes.push(buffer);
      buffer = "";
    }
  };

  while (index < text.length) {
    const char = text[index];

    if (char === "`") {
      const end = text.indexOf("`", index + 1);
      if (end === -1) {
        buffer += char;
        index += 1;
        continue;
      }
      flush();
      nodes.push(
        <code
          key={nodes.length}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]"
        >
          {text.slice(index + 1, end)}
        </code>,
      );
      index = end + 1;
      continue;
    }

    if (char === "*" && text[index + 1] === "*") {
      const end = text.indexOf("**", index + 2);
      if (end === -1) {
        buffer += char;
        index += 1;
        continue;
      }
      flush();
      nodes.push(
        <strong key={nodes.length}>{text.slice(index + 2, end)}</strong>,
      );
      index = end + 2;
      continue;
    }

    if (char === "*") {
      const end = text.indexOf("*", index + 1);
      if (end === -1) {
        buffer += char;
        index += 1;
        continue;
      }
      flush();
      nodes.push(<em key={nodes.length}>{text.slice(index + 1, end)}</em>);
      index = end + 1;
      continue;
    }

    buffer += char;
    index += 1;
  }

  flush();
  return nodes;
}

/**
 * Minimal, XSS-safe Markdown renderer (bold / italic / inline code / fenced
 * code blocks / headings / lists). Renders React elements only — never
 * `dangerouslySetInnerHTML` — so assistant output cannot inject markup.
 * Incomplete markdown (an unclosed fence or emphasis) degrades to literal text.
 */
export function MarkdownText({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let key = 0;

  for (let index = 0; index < lines.length; ) {
    const line = lines[index];

    if (isFence(line)) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !isFence(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1; // consume the closing fence
      blocks.push(
        <pre
          key={key++}
          className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-sm"
        >
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = BLOCK_OPEN_RE.exec(line);
    if (heading) {
      const level = heading[1].length;
      const content = line.slice(heading[0].length);
      const size =
        level <= 2
          ? "text-base font-semibold"
          : "text-sm font-semibold";
      blocks.push(
        <p key={key++} className={size}>
          {renderInline(content)}
        </p>,
      );
      index += 1;
      continue;
    }

    if (UNORDERED_RE.test(line)) {
      const items: string[] = [];
      while (index < lines.length && UNORDERED_RE.test(lines[index])) {
        items.push(lines[index].replace(UNORDERED_RE, ""));
        index += 1;
      }
      blocks.push(
        <ul key={key++} className="list-disc space-y-1 pl-5">
          {items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (ORDERED_RE.test(line)) {
      const items: string[] = [];
      while (index < lines.length && ORDERED_RE.test(lines[index])) {
        items.push(lines[index].replace(ORDERED_RE, ""));
        index += 1;
      }
      blocks.push(
        <ol key={key++} className="list-decimal space-y-1 pl-5">
          {items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    if (line.trim() === "") {
      index += 1;
      continue;
    }

    const parts: string[] = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() !== "" &&
      !isBlockStart(lines[index])
    ) {
      parts.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={key++} className="leading-relaxed">
        {renderInline(parts.join(" "))}
      </p>,
    );
  }

  return <div className="space-y-2 text-sm text-foreground">{blocks}</div>;
}
