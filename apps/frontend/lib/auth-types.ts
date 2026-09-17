/**
 * Authentication domain types.
 *
 * The backend wraps successful responses in `{ code, data, message }` (see the
 * shared `@eap/api-contract` package). `ApiEnvelope` is redeclared here until
 * the frontend consumes the contract package directly.
 */

/** Current tenant. Kept minimal — extend once `/api/me` returns tenant detail. */
export interface Tenant {
  id: string;
}

/** Authenticated user profile returned by `GET /api/me`. */
export interface CurrentUser {
  id: string;
  tenantId: string;
  email: string;
  name: string;
  department: string | null;
  role: string;
  avatarText: string | null;
}

/** Standard success envelope; `code === 0` means success. */
export interface ApiEnvelope<T> {
  code: number;
  data: T;
  message: string;
}
