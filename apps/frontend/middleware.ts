import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { canAccessAdmin, resolveRole, ROLE_COOKIE } from "@/lib/roles";

/**
 * Server-side authorization for the admin workspace (architecture v8.0 §31.2.2).
 * Runs before route rendering, so a direct request or RSC fetch cannot bypass
 * the check by skipping client hydration. Employees are served the friendly
 * permission notice at the original `/admin` URL.
 */
export function middleware(request: NextRequest) {
  const role = resolveRole(request.cookies.get(ROLE_COOKIE)?.value);
  if (!canAccessAdmin(role)) {
    return NextResponse.rewrite(new URL("/forbidden", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/admin/:path*",
};
