import axios from "axios";

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

/**
 * Stable machine-readable `error.code` from a failed request, if the backend
 * sent one (C-5).
 *
 * Lives apart from the service modules so the refresh channel can classify a
 * rejection without importing the API layer it sits underneath.
 */
export function extractApiErrorCode(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) return undefined;
  const envelope = error.response?.data as ErrorEnvelope | undefined;
  return envelope?.error?.code;
}
