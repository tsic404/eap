/**
 * Shared API contract between the EAP frontend and backend.
 *
 * The backend wraps successful application responses in a standard envelope
 * (see backend-dev-standards: `{ code, data, message }`), and errors are
 * normalized by the global exception handler. Health probes are an explicit
 * raw-response exception: they return plain objects (not the envelope) so that
 * load balancers and orchestrators can consume them directly, and the matching
 * client response types below reflect that shape.
 */

/** Standard successful-response envelope. `code === 0` means success. */
export interface ApiEnvelope<T> {
  code: number;
  data: T;
  message: string;
}

/** Standard structured error response. */
export interface ApiError {
  code: number;
  message: string;
  details?: unknown;
}

/** Liveness probe response (`GET /api/health/live`). */
export interface HealthLiveResponse {
  status: "ok";
}

/** Basic service info (`GET /api/health`). */
export interface HealthInfoResponse {
  service: string;
  version: string;
  status: "ok";
}

/** Default HTTP error codes shared across the platform. */
export const ErrorCode = {
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  RATE_LIMITED: 429,
  INTERNAL: 500,
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];
