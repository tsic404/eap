"use client";

import { createContext, useContext } from "react";

import type { UserRole } from "@/lib/roles";

const RoleContext = createContext<UserRole>("employee");

export interface RoleProviderProps {
  role: UserRole;
  children: React.ReactNode;
}

/** Provides the current user's role (least-privilege default: employee). */
export function RoleProvider({ role, children }: RoleProviderProps) {
  return <RoleContext.Provider value={role}>{children}</RoleContext.Provider>;
}

/** Current user's role; falls back to employee outside a provider. */
export function useRole(): UserRole {
  return useContext(RoleContext);
}
