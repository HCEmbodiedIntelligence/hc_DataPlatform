import { request } from "../../../shared/api/http-client";
import type { components } from "../../../shared/api/generated/platform";
import {
  CollectionReleasePreflightWire,
  CollectionReleaseWire,
} from "../../../shared/api/generated/platform-wires";
import type { Scope } from "../../../shared/scope/model";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { parseWire } from "../../../shared/api/validate";

// Identity constraints are domain rules. Wire fields, enums, hashes and limits
// come from the production contract; cross-receipt checks remain below.
function validReleaseIdentity(
  value: CollectionReleasePreflight | CollectionRelease,
) {
  return (
    Boolean(value.dataset_id) &&
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/u.test(value.dataset_version) &&
    /^cv_[0-9a-f]{64}$/u.test(value.collection_version_id) &&
    value.eligible_episode_ids.every(Boolean) &&
    value.excluded_episodes.every((item) => Boolean(item.episode_id))
  );
}
const preflightWire = CollectionReleasePreflightWire.refine(
  validReleaseIdentity,
).refine((value) =>
  Object.entries(value.annotation_task_options).every(
    ([id, tasks]) => Boolean(id) && tasks.every(Boolean),
  ),
);
const releaseWire = CollectionReleaseWire.refine(validReleaseIdentity);

export type CollectionReleaseCommand =
  components["schemas"]["PublishCollectionRelease"];
export type CollectionReleasePreflight =
  components["schemas"]["CollectionReleasePreflight"];
export type CollectionRelease = components["schemas"]["CollectionRelease"];

export function workingCollectionId(versionId: string | null): string | null {
  return versionId?.match(/^version_(cv_[0-9a-f]{64})$/u)?.[1] ?? null;
}

function endpoint(scope: Scope, datasetId: string) {
  if (
    !scope.organizationId ||
    !scope.projectId ||
    !scope.regionCode ||
    !datasetId
  )
    throw new Error("发布需要明确的组织、项目、区域和数据集。");
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/datasets/${encodeURIComponent(datasetId)}/collection-releases`;
}

function check(
  value: CollectionReleasePreflight | CollectionRelease,
  datasetId: string,
  command: CollectionReleaseCommand,
) {
  const ids = [
    ...value.eligible_episode_ids,
    ...value.excluded_episodes.map((e) => e.episode_id),
  ];
  if (
    value.dataset_id !== datasetId ||
    value.dataset_version !== command.dataset_version ||
    value.collection_version_id !== command.collection_version_id ||
    new Set(ids).size !== ids.length ||
    (command.expected_content_hash &&
      value.content_hash !== command.expected_content_hash)
  )
    throw new Error("发布回执与固定集合、预检内容或数据集身份不一致。");
  if (
    command.episode_ids &&
    (command.episode_ids.some((episode) => !ids.includes(episode)) ||
      value.eligible_episode_ids.some(
        (episode) => !command.episode_ids!.includes(episode),
      ) ||
      value.excluded_episodes.some(
        (item) =>
          (item.reason === "NOT_SELECTED") ===
          command.episode_ids!.includes(item.episode_id),
      ))
  )
    throw new Error("发布回执与明确选择的 Episode 不一致。");
  if ("annotation_task_options" in value) {
    const choices = value.annotation_task_options;
    if (
      Object.keys(choices).length !== ids.length ||
      ids.some((episode) => !Object.hasOwn(choices, episode)) ||
      Object.values(choices).some(
        (tasks) => new Set(tasks).size !== tasks.length,
      ) ||
      value.eligible_episode_ids.some((episode) => {
        const tasks = choices[episode]!;
        const selected = command.annotation_tasks?.[episode];
        return selected ? !tasks.includes(selected) : tasks.length !== 1;
      })
    )
      throw new Error("预检的 Episode 与已审核标注任务选择不一致。");
  }
  return value;
}

export async function preflightCollectionRelease(
  scope: Scope,
  datasetId: string,
  command: CollectionReleaseCommand,
  signal?: AbortSignal,
) {
  const path = `${endpoint(scope, datasetId)}/preflight`;
  const value = parseWire(
    preflightWire,
    await request({
      method: "POST",
      path,
      scope,
      scopeMode: "explicit",
      body: command,
      signal,
      cache: "no-store",
    }),
    { endpoint: path },
  );
  check(value, datasetId, command);
  return value;
}

export async function publishCollectionRelease(
  scope: Scope,
  datasetId: string,
  command: CollectionReleaseCommand,
  signal?: AbortSignal,
) {
  if (!command.expected_content_hash)
    throw new Error("请先预检，再发布已核对的内容。");
  if (!command.episode_ids?.length)
    throw new Error("请明确选择本次发布的 Episode。");
  const path = endpoint(scope, datasetId);
  const value = parseWire(
    releaseWire,
    await request({
      method: "POST",
      path,
      scope,
      scopeMode: "explicit",
      body: command,
      signal,
      cache: "no-store",
    }),
    { endpoint: path },
  );
  check(value, datasetId, command);
  if (!value.eligible_episode_ids.length)
    throw new Error("发布回执没有已审核 Episode。");
  return value;
}

export async function loadCollectionRelease(
  scope: Scope,
  datasetId: string,
  versionId: string,
  signal?: AbortSignal,
) {
  const path = `${endpoint(scope, datasetId)}/${encodeURIComponent(versionId)}`;
  const value = parseWire(
    releaseWire,
    await request({
      method: "GET",
      path,
      scope,
      scopeMode: "explicit",
      signal,
      cache: "no-store",
    }),
    { endpoint: path },
  );
  if (value.dataset_id !== datasetId || value.version_id !== versionId)
    throw new Error("发布版本读取身份不一致。");
  return value;
}

/** Read back the immutable receipt in the same login/grant lifetime as publication. */
export async function publishAndReadCollectionRelease(
  scope: Scope,
  datasetId: string,
  command: CollectionReleaseCommand,
  signal?: AbortSignal,
) {
  const lease = createSessionScopeLease(scope, signal);
  try {
    const receipt = await publishCollectionRelease(
      scope,
      datasetId,
      command,
      lease.signal,
    );
    lease.signal.throwIfAborted();
    const persisted = await loadCollectionRelease(
      scope,
      datasetId,
      receipt.version_id,
      lease.signal,
    );
    check(persisted, datasetId, command);
    if (
      persisted.content_hash !== receipt.content_hash ||
      persisted.document_sha256 !== receipt.document_sha256 ||
      persisted.document_size !== receipt.document_size ||
      persisted.collection_manifest_sha256 !==
        receipt.collection_manifest_sha256 ||
      JSON.stringify(persisted.eligible_episode_ids) !==
        JSON.stringify(receipt.eligible_episode_ids) ||
      JSON.stringify(persisted.excluded_episodes) !==
        JSON.stringify(receipt.excluded_episodes)
    )
      throw new Error("已发布版本的读取回执不一致，请重试查询。");
    return persisted;
  } finally {
    lease.release();
  }
}
