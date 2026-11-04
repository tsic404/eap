"use client";

import { createContext, useContext } from "react";

import type { UserRole } from "@/lib/roles";

const RoleContext = createContext<UserRole>("employee");

/**
 * Role the authenticated session resolved from the profile read, published by
 * the auth provider. `null` until that read answers.
 */
const SessionRoleContext = createContext<UserRole | null>(null);

export interface RoleProviderProps {
  /**
   * Fallback role from the request cookie, read while this document rendered.
   * A session role already published above wins over it (`SessionRoleProvider`
   * with `role={null}` pins the value for surfaces that must not follow the
   * session, e.g. component-gallery samples).
   */
  role: UserRole;
  children: React.ReactNode;
}

export interface SessionRoleProviderProps {
  role: UserRole | null;
  children: React.ReactNode;
}

/**
 * Publishes the session's role so role-driven surfaces follow it live. It
 * overrides every `RoleProvider` nested inside it, including the root layout's;
 * `null` leaves the nearest `RoleProvider` role in force.
 */
export function SessionRoleProvider({ role, children }: SessionRoleProviderProps) {
  return <SessionRoleContext.Provider value={role}>{children}</SessionRoleContext.Provider>;
}

/**
 * Resolves the role every role-driven surface reads: the session's role once
 * the profile read answers, `role` until then — never the other way round, so
 * a caller must not pass a value it expects to survive the session.
 * SSO lands on the user workspace before the browser has written `eap_role`,
 * so that document renders the least-privilege role and only the profile read
 * knows the account's own.
 */
export function RoleProvider({ role: documentRole, children }: RoleProviderProps) {
  const sessionRole = useContext(SessionRoleContext);
  return (
    <RoleContext.Provider value={sessionRole ?? documentRole}>
      {children}
    </RoleContext.Provider>
  );
}

/** Current user's role; falls back to employee outside a provider. */
export function useRole(): UserRole {
  return useContext(RoleContext);
}
