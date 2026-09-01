import type { IngestScope } from "../../entities/data-source";
import {
  collectionTaskGateway,
  type CollectionTask,
} from "../p20-collection-tasks/api";

const PAGE_SIZE = 100;

export async function listActiveCollectionTasks(
  scope: IngestScope,
  signal?: AbortSignal,
): Promise<readonly CollectionTask[]> {
  const tasks: CollectionTask[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;

  do {
    const page = await collectionTaskGateway.list(
      scope,
      {
        status: "ACTIVE",
        limit: PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      },
      signal,
    );
    tasks.push(...page.items);

    const nextCursor = page.next_cursor ?? undefined;
    if (nextCursor && seenCursors.has(nextCursor)) {
      throw new Error("采集任务候选项分页游标重复，已停止加载。");
    }
    if (nextCursor) seenCursors.add(nextCursor);
    cursor = nextCursor;
  } while (cursor);

  return tasks.sort(
    (left, right) =>
      left.name.localeCompare(right.name, "zh-CN") ||
      left.collection_task_id.localeCompare(right.collection_task_id),
  );
}
