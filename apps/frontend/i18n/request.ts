import { cookies } from "next/headers";

import { hasLocale } from "next-intl";
import { getRequestConfig } from "next-intl/server";

import { LOCALES, DEFAULT_LOCALE, LOCALE_COOKIE } from "./routing";

/**
 * Request-scoped locale for the single-locale (non-prefixed) routing setup.
 * The locale lives in a cookie so the browser carries it across navigations
 * without a `[locale]` path segment; the locale switcher rewrites the cookie
 * and refreshes. Falls back to the default locale for absent/invalid values.
 */
export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const requested = cookieStore.get(LOCALE_COOKIE)?.value;
  const locale = hasLocale(LOCALES, requested) ? requested : DEFAULT_LOCALE;

  return {
    locale,
    // Dynamic import is required here: the locale is selected per-request from
    // a cookie, so the message catalog path cannot be resolved at build time.
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
