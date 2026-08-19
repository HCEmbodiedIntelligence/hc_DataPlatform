// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  clearTagDraftRecovery,
  loadTagDraftRecovery,
  saveTagDraftRecovery,
} from "./tag-draft-recovery";
import { visualTag } from "./testing/annotation-fixture";

const identity = {
  principalId: "annotator-a",
  projectId: "project-a",
  taskId: "task-a",
  taskEtag: '"task-a-v2"',
  revision: 2,
} as const;

describe("P08 session-only Tag draft recovery", () => {
  beforeEach(() => sessionStorage.clear());

  it("restores only the exact principal, task ETag and revision before TTL", () => {
    saveTagDraftRecovery(identity, [visualTag], 1_000);
    expect(loadTagDraftRecovery(identity, 2_000)).toEqual([visualTag]);
    expect(
      loadTagDraftRecovery({ ...identity, taskEtag: '"task-a-v3"' }, 2_000),
    ).toBeNull();
  });

  it("expires and explicitly clears a recovery draft", () => {
    saveTagDraftRecovery(identity, [visualTag], 1_000);
    expect(
      loadTagDraftRecovery(identity, 1_000 + 2 * 60 * 60 * 1000 + 1),
    ).toBeNull();
    saveTagDraftRecovery(identity, [visualTag], 2_000);
    clearTagDraftRecovery(identity);
    expect(loadTagDraftRecovery(identity, 2_001)).toBeNull();
  });
});
