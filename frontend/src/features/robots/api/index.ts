/** Compatibility entry point. Reusable workspaces import client.ts or explicit hooks.ts. */
import {
  getCurrentOrganizationId,
  useCurrentOrganizationId,
} from "../../../shared/scope/current-organization";
import * as hooks from "./hooks";
import { getRobotBootstrap as getScopedRobotBootstrap } from "./client";
export * from "./client";
export * from "./hooks";

/** Legacy session adapter. New clients must pass organizationId explicitly. */
export function getRobotBootstrap(robotId: string, signal?: AbortSignal) {
  return getScopedRobotBootstrap(getCurrentOrganizationId(), robotId, signal);
}
export function useRobots(filters: Readonly<Record<string, string>> = {}) {
  return hooks.useScopedRobots(useCurrentOrganizationId(), filters);
}
export function useRobotBootstrap(robotId: string | null) {
  return hooks.useScopedRobotBootstrap(useCurrentOrganizationId(), robotId);
}
export function useCreateRobot() {
  return hooks.useScopedCreateRobot(useCurrentOrganizationId());
}
export function useUpdateRobot() {
  return hooks.useScopedUpdateRobot(useCurrentOrganizationId());
}
export function useTransitionRobotLifecycle() {
  return hooks.useScopedTransitionRobotLifecycle(useCurrentOrganizationId());
}
export function useDeleteRobot() {
  return hooks.useScopedDeleteRobot(useCurrentOrganizationId());
}
