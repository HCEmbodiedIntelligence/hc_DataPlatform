import { z } from "zod";
import { request } from "../../shared/api/http-client";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import {
  useScopedMutation,
  invalidateScopedDomain,
} from "../../shared/api/use-scoped-mutation";
import { useQueryClient } from "@tanstack/react-query";
import type { CollectionTaskScope } from "../collection-tasks/api";
import { createDomainError } from "../../shared/api/domain-error";
import {
  detailSchema,
  linkSchema,
  referencesSchema,
  summarySchema,
  versionSchema,
  taskPlanSchema,
  type SetTaskPlan,
  type AssociateCommand,
  type Sample,
  type SchemeDraft,
} from "./contracts";
const root = (org: string) => `/organizations/${encodeURIComponent(org)}`;
const schemePath = (org: string, id: string) =>
  `${root(org)}/capture-schemes/${encodeURIComponent(id)}`;
const linksPath = (org: string, robot: string) =>
  `${root(org)}/robots/${encodeURIComponent(robot)}/capture-schemes`;
async function call(
  org: string,
  method: "GET" | "PUT" | "POST",
  path: string,
  body?: unknown,
  query?: Record<string, unknown>,
  signal?: AbortSignal,
) {
  return request({
    method,
    path,
    scope: { organizationId: org },
    scopeMode: "organization",
    body,
    query,
    signal,
  });
}
export const captureApi = {
  version: async (
    org: string,
    id: string,
    version: number,
    signal?: AbortSignal,
  ) =>
    versionSchema.parse(
      await call(
        org,
        "GET",
        `${schemePath(org, id)}/versions/${version}`,
        undefined,
        undefined,
        signal,
      ),
    ),
  list: async (
    org: string,
    query: Record<string, unknown> = {},
    signal?: AbortSignal,
  ) =>
    z
      .object({ items: z.array(summarySchema) })
      .parse(
        await call(
          org,
          "GET",
          `${root(org)}/capture-schemes`,
          undefined,
          query,
          signal,
        ),
      ).items,
  detail: async (org: string, id: string, signal?: AbortSignal) =>
    detailSchema.parse(
      await call(org, "GET", schemePath(org, id), undefined, undefined, signal),
    ),
  save: async (org: string, id: string, command: SchemeDraft) =>
    detailSchema.parse(await call(org, "PUT", schemePath(org, id), command)),
  validate: async (
    org: string,
    id: string,
    expected_revision: number,
    sample: Sample,
  ) =>
    detailSchema.parse(
      await call(org, "POST", `${schemePath(org, id)}:validate`, {
        expected_revision,
        sample,
      }),
    ),
  publish: async (org: string, id: string, expected_revision: number) =>
    versionSchema.parse(
      await call(org, "POST", `${schemePath(org, id)}:publish`, {
        expected_revision,
      }),
    ),
  archive: async (org: string, id: string, expected_revision: number) =>
    detailSchema.parse(
      await call(org, "POST", `${schemePath(org, id)}:archive`, {
        expected_revision,
      }),
    ),
  references: async (org: string, id: string, signal?: AbortSignal) =>
    referencesSchema.parse(
      await call(
        org,
        "GET",
        `${schemePath(org, id)}/references`,
        undefined,
        undefined,
        signal,
      ),
    ),
  links: async (org: string, robot: string, signal?: AbortSignal) =>
    z
      .object({ items: z.array(linkSchema) })
      .parse(
        await call(
          org,
          "GET",
          linksPath(org, robot),
          undefined,
          undefined,
          signal,
        ),
      ).items,
  associate: async (org: string, robot: string, command: AssociateCommand) =>
    z
      .object({ items: z.array(linkSchema) })
      .parse(await call(org, "PUT", linksPath(org, robot), command)).items,
  validateMapping: async (
    org: string,
    robot: string,
    scheme: string,
    mapping_version: number,
    sample: Sample,
  ) =>
    z
      .object({ items: z.array(linkSchema) })
      .parse(
        await call(
          org,
          "POST",
          `${linksPath(org, robot)}/${encodeURIComponent(scheme)}:validate`,
          { mapping_version, sample },
        ),
      ).items,
  remove: async (
    org: string,
    robot: string,
    scheme: string,
    expected_revision: number,
  ) =>
    call(
      org,
      "POST",
      `${linksPath(org, robot)}/${encodeURIComponent(scheme)}:remove`,
      { expected_revision },
    ),
};

export async function taskPlanRequest(
  scope: CollectionTaskScope,
  taskId: string,
  command?: { kind: "replace"; plan: SetTaskPlan },
  signal?: AbortSignal,
) {
  const result = z.object({ plan: taskPlanSchema.nullable() }).parse(
    await request({
      scope,
      method: command ? "PUT" : "GET",
      path: `/projects/${encodeURIComponent(scope.projectId)}/collection-tasks/${encodeURIComponent(taskId)}/capture-plan`,
      ...(signal ? { signal } : {}),
      ...(command
        ? {
            body: command.plan,
          }
        : {}),
    }),
  );
  if (
    result.plan &&
    (result.plan.organization_id !== scope.organizationId ||
      result.plan.project_id !== scope.projectId ||
      result.plan.region_code !== scope.regionCode ||
      result.plan.collection_task_id !== taskId)
  )
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "任务采集计划与当前范围不匹配。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  return result;
}
export function useCaptureQuery<T>(
  org: string | null,
  resource: string,
  identity: unknown,
  queryFn: (signal: AbortSignal) => Promise<T>,
  enabled = true,
) {
  return useScopedQuery(
    org ? { organizationId: org } : null,
    "capture-schemes",
    resource,
    identity,
    {
      queryFn: ({ signal }) => queryFn(signal),
      enabled: Boolean(org) && enabled,
      retry: false,
    },
  );
}
export function useCaptureMutation<T, V>(
  org: string,
  fn: (value: V) => Promise<T>,
) {
  const client = useQueryClient();
  return useScopedMutation(
    { organizationId: org },
    (_scope, value: V) => fn(value),
    {
      onSuccess: (_data, _variables, binding) => {
        void invalidateScopedDomain(client, binding, "capture-schemes");
      },
    },
  );
}
