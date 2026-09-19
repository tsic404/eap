"use client";

import useSWR from "swr";

import { getAdminDashboard, getUserHome, listModels } from "./dashboard-service";

/** SWR-backed admin dashboard aggregate (`GET /api/dashboard/admin`). */
export function useAdminDashboard() {
  return useSWR("dashboard:admin", getAdminDashboard);
}

/** SWR-backed user home aggregate (`GET /api/dashboard/user`). */
export function useUserHome() {
  return useSWR("dashboard:user", getUserHome);
}

/** SWR-backed Dify model provider list (`GET /api/models`). */
export function useModels() {
  return useSWR("models", listModels);
}
