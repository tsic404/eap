/** Supported UI locales (§30.8). Only `zh-CN` until the remaining hard-coded
 * strings are migrated — exposing a second locale would render a half-mixed UI. */
export const LOCALES = ["zh-CN"] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "zh-CN";

/** Cookie carrying the user's UI locale (mirrors next-intl's `NEXT_LOCALE`). */
export const LOCALE_COOKIE = "NEXT_LOCALE";
