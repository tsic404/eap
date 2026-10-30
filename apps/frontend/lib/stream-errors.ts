/**
 * Error classification shared by every SSE consumer.
 *
 * A stream endpoint fails in two shapes: a JSON envelope (``{error: {code,
 * message}}``) when preparation fails before the response headers, or an
 * ``error`` frame once the stream is open (C-55 / C-101). The conversation and
 * the agent debug turn answer the same way, so the mapping lives here once.
 */

export interface StreamErrorEnvelope {
  code: string | null;
  message: string | null;
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Read the JSON error envelope a failed stream preparation returns. */
export async function decodeErrorEnvelope(
  response: Response,
): Promise<StreamErrorEnvelope> {
  const body = (await response.json().catch(() => null)) as ErrorEnvelope | null;
  return { code: body?.error?.code ?? null, message: body?.error?.message ?? null };
}

/**
 * Describe a non-SSE response: a caller-supplied message for a recognised error
 * code (the backend's messages are not user-facing), else the envelope's own
 * message, else the HTTP status.
 */
export async function describeHttpError(
  response: Response,
  contentType: string,
  messagesByCode: Record<string, string> = {},
): Promise<string> {
  if (contentType.includes("application/json")) {
    const info = await decodeErrorEnvelope(response);
    const known = info.code ? messagesByCode[info.code] : undefined;
    if (known) return known;
    if (info.message) return info.message;
  }
  return `请求失败（HTTP ${response.status}）`;
}

/**
 * Describe the in-stream ``error`` frame (C-101): a caller-supplied message for
 * a recognised error code, else the envelope's own message, else the code.
 */
export function describeStreamError(
  data: Record<string, unknown>,
  messagesByCode: Record<string, string> = {},
): string {
  const code = typeof data.code === "string" ? data.code : null;
  const known = code ? messagesByCode[code] : undefined;
  if (known) return known;
  const message = typeof data.message === "string" ? data.message : null;
  return message ?? code ?? "对话生成出错";
}
