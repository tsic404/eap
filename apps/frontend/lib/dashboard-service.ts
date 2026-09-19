/**
 * Dashboard API access layer. All functions return the unwrapped `data` payload
 * of the backend's standard `{ code, data, message }` envelope; errors surface
 * as Axios errors so callers can branch on `response.status`.
 */

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import type {
  AdminDashboard,
  ModelProvider,
  UserHome,
} from "./dashboard-types";
import { apiClient } from "./http-client";

export async function getAdminDashboard(): Promise<AdminDashboard> {
  const response = await apiClient.get<ApiEnvelope<AdminDashboard>>(
    API_ROUTES.dashboardAdmin,
  );
  return response.data.data;
}

export async function getUserHome(): Promise<UserHome> {
  const response = await apiClient.get<ApiEnvelope<UserHome>>(
    API_ROUTES.dashboardUser,
  );
  return response.data.data;
}

export async function listModels(): Promise<ModelProvider[]> {
  const response = await apiClient.get<ApiEnvelope<ModelProvider[]>>(
    API_ROUTES.models,
  );
  return response.data.data;
}
