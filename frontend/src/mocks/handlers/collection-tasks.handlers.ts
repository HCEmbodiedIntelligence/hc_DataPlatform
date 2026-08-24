import { http, HttpResponse } from "msw";
import type { components } from "../../shared/api/generated/platform";
import { getManagementScenario } from "../scenarios/management";

type CollectionTask = components["schemas"]["CollectionTask"];
type CollectionTaskProgress = components["schemas"]["CollectionTaskProgress"];

const root = "*/api/v1/projects/:projectId/collection-tasks";
const closePath =
  /\/api\/v1\/projects\/(?<projectId>[^/]+)\/collection-tasks\/(?<taskId>[^/:]+):close$/u;
const cancelPath =
  /\/api\/v1\/projects\/(?<projectId>[^/]+)\/collection-tasks\/(?<taskId>[^/:]+):cancel$/u;
const reopenPath =
  /\/api\/v1\/projects\/(?<projectId>[^/]+)\/collection-tasks\/(?<taskId>[^/:]+):reopen$/u;

function initialTasks(
  organizationId: string,
  projectId: string,
): CollectionTask[] {
  return [
    {
      schema_version: "1",
      collection_task_id: "collection-task-fx-01",
      organization_id: organizationId,
      project_id: projectId,
      task_code: "00000042",
      name: "透明件抓取多视角采集",
      type: "抓取采集",
      scenario: "透明工件装配工位",
      description: "覆盖反光、遮挡与不同夹爪姿态。",
      target: { package_count: 240 },
      quality_threshold: 0.9,
      status: "ACTIVE",
    },
    {
      schema_version: "1",
      collection_task_id: "collection-task-fx-02",
      organization_id: organizationId,
      project_id: projectId,
      task_code: "00000039",
      name: "托盘搬运夜班数据补采",
      type: "搬运采集",
      scenario: "低照度仓储通道",
      description: "补充低照度、逆光和动态人员干扰样本。",
      target: { package_count: 180 },
      quality_threshold: 0.86,
      status: "ACTIVE",
    },
  ];
}

let tasksByScope = new Map<string, CollectionTask[]>();

function organizationFrom(request: Request): string {
  return request.headers.get("X-Organization-Id") || "organization-mock";
}

function tasksFor(organizationId: string, projectId: string): CollectionTask[] {
  const key = `${organizationId}/${projectId}`;
  const current = tasksByScope.get(key);
  if (current) return current;
  const created = initialTasks(organizationId, projectId);
  tasksByScope.set(key, created);
  return created;
}

function forbidden(): Response | null {
  if (getManagementScenario() !== "forbidden") return null;
  return HttpResponse.json(
    {
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "Collection task access is not granted in this fixture.",
      code: "COLLECTION_TASK_FORBIDDEN",
      request_id: "req_fx_collection_task_403",
      retryable: false,
    },
    { status: 403 },
  );
}

function progress(task: CollectionTask): CollectionTaskProgress {
  const received = task.collection_task_id.endsWith("02") ? 98 : 146;
  const evaluated = received - 7;
  const passed = evaluated - 8;
  return {
    schema_version: "1",
    collection_task_id: task.collection_task_id,
    organization_id: task.organization_id,
    project_id: task.project_id,
    status: task.status,
    as_of: "2026-08-18T05:30:00Z",
    received_package_count: received,
    captured_duration_seconds: received * 75,
    duration_observed_package_count: received,
    duration_unknown_package_count: 0,
    qc: {
      evaluated_count: evaluated,
      pass_count: passed,
      risk_count: evaluated - passed,
      reject_count: 0,
      pending_count: received - evaluated,
      pass_rate: {
        numerator: passed,
        denominator: evaluated,
        value: evaluated > 0 ? passed / evaluated : null,
      },
    },
    attainment: {
      status: task.target ? "IN_PROGRESS" : "NOT_CONFIGURED",
      package_count:
        task.target?.package_count == null
          ? null
          : {
              actual: received,
              target: task.target.package_count,
              progress: received / task.target.package_count,
              status: "IN_PROGRESS",
            },
      duration_seconds: null,
      quality_threshold: task.quality_threshold,
      quality_status:
        task.quality_threshold == null ? "NOT_CONFIGURED" : "PENDING_QC",
    },
    observed_sources: {
      device_ids: ["robot_fx_01"],
      camera_ids: ["camera-front"],
      topic_names: ["/joint_states", "/camera/front/image"],
    },
  };
}

export const collectionTaskHandlers = [
  http.get(root, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const organizationId = organizationFrom(request);
    const status = new URL(request.url).searchParams.get("status");
    const items = tasksFor(organizationId, projectId).filter(
      (task) => status === null || task.status === status,
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),
  http.get(`${root}/:taskId/progress`, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const task = tasksFor(organizationFrom(request), projectId).find(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    return task
      ? HttpResponse.json(progress(task))
      : HttpResponse.json(
          { detail: "Collection task not found." },
          { status: 404 },
        );
  }),
  http.get(`${root}/:taskId`, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const task = tasksFor(organizationFrom(request), projectId).find(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    return task
      ? HttpResponse.json(task, {
          headers: { ETag: '"collection-task-fx-v1"' },
        })
      : HttpResponse.json(
          { detail: "Collection task not found." },
          { status: 404 },
        );
  }),
  http.post(root, async ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const organizationId = organizationFrom(request);
    const body =
      (await request.json()) as components["schemas"]["CreateCollectionTask"];
    const tasks = tasksFor(organizationId, projectId);
    const task: CollectionTask = {
      ...body,
      quality_threshold: body.quality_threshold ?? null,
      target: body.target ?? null,
      schema_version: "1",
      collection_task_id: `collection-task-fx-${tasks.length + 1}`,
      organization_id: organizationId,
      project_id: projectId,
      task_code: String(43 + tasks.length).padStart(8, "0"),
      status: "ACTIVE",
    };
    tasks.push(task);
    return HttpResponse.json(task, { status: 201 });
  }),
  http.patch(`${root}/:taskId`, async ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const tasks = tasksFor(organizationFrom(request), projectId);
    const index = tasks.findIndex(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    if (index < 0)
      return HttpResponse.json(
        { detail: "Collection task not found." },
        { status: 404 },
      );
    const body =
      (await request.json()) as components["schemas"]["UpdateCollectionTask"];
    const updated = { ...tasks[index]!, ...body };
    tasks[index] = updated;
    return HttpResponse.json(updated, {
      headers: { ETag: '"collection-task-fx-v2"' },
    });
  }),
  http.post(closePath, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const tasks = tasksFor(organizationFrom(request), projectId);
    const index = tasks.findIndex(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    if (index < 0)
      return HttpResponse.json(
        { detail: "Collection task not found." },
        { status: 404 },
      );
    const closed: CollectionTask = { ...tasks[index]!, status: "CLOSED" };
    tasks[index] = closed;
    return HttpResponse.json(closed);
  }),
  http.post(cancelPath, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const tasks = tasksFor(organizationFrom(request), projectId);
    const index = tasks.findIndex(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    if (index < 0)
      return HttpResponse.json(
        { detail: "Collection task not found." },
        { status: 404 },
      );
    const cancelled: CollectionTask = {
      ...tasks[index]!,
      status: "CANCELLED",
    };
    tasks[index] = cancelled;
    return HttpResponse.json(cancelled);
  }),
  http.post(reopenPath, ({ request, params }) => {
    const denied = forbidden();
    if (denied) return denied;
    const projectId = String(params.projectId);
    const tasks = tasksFor(organizationFrom(request), projectId);
    const index = tasks.findIndex(
      (candidate) => candidate.collection_task_id === String(params.taskId),
    );
    if (index < 0)
      return HttpResponse.json(
        { detail: "Collection task not found." },
        { status: 404 },
      );
    const reopened: CollectionTask = { ...tasks[index]!, status: "ACTIVE" };
    tasks[index] = reopened;
    return HttpResponse.json(reopened);
  }),
];

export function resetCollectionTaskHandlerState(): void {
  tasksByScope = new Map();
}

export default collectionTaskHandlers;
