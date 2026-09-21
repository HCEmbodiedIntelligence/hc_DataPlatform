import { describe, expect, it } from "vitest";
import {
  reconcileTagHierarchy,
  removeIntervalTag,
  updateIntervalTag,
} from "./tag-editing";
import type { RuntimeAnnotationTag } from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualTag,
} from "./testing/annotation-fixture";
import { evaluateAnnotationTags } from "./tag-validation";

const child: RuntimeAnnotationTag = {
  annotation_id: "child",
  tag_id: "manual-child",
  label: "夹爪闭合",
  parent_annotation_id: visualTag.annotation_id,
  path: [...visualTag.path, "manual-child"],
  start_step: 330,
  end_step: 420,
  attributes: {},
  relations: [],
  subject: null,
};
const grandchild: RuntimeAnnotationTag = {
  ...child,
  annotation_id: "grandchild",
  tag_id: "manual-grandchild",
  label: "闭合完成",
  parent_annotation_id: child.annotation_id,
  path: [...child.path, "manual-grandchild"],
  start_step: 350,
  end_step: 400,
};

function expectValid(tags: readonly RuntimeAnnotationTag[]) {
  const bundle = createVisualAnnotationBundle({ mode: "annotation" });
  expect(
    evaluateAnnotationTags({
      task: bundle.task,
      schema: bundle.schema,
      tags,
    }).every((check) => check.status === "PASS"),
  ).toBe(true);
}

describe("Tag edits", () => {
  it("keeps identities and metadata when renaming a schema Tag and repairs descendant paths", () => {
    const tags = [grandchild, child, visualTag];
    const next = updateIntervalTag(tags, visualTag.annotation_id, {
      label: "抓取物品",
      start_step: 290,
      end_step: 480,
    });
    expect(next[2]).toEqual({
      ...visualTag,
      label: "抓取物品",
      start_step: 290,
      end_step: 480,
      path: [visualTag.tag_id],
    });
    expect(next[0]?.path).toEqual([
      visualTag.tag_id,
      child.tag_id,
      grandchild.tag_id,
    ]);
    expect(tags[2]).toBe(visualTag);
    expect(child.path).toEqual([...visualTag.path, child.tag_id]);
    expectValid(next);
  });

  it("promotes the longer interval when resizing it past its former parent", () => {
    const next = updateIntervalTag([visualTag, child], child.annotation_id, {
      start_step: 280,
      end_step: 500,
    });
    expect(next[0]).toEqual({
      ...visualTag,
      parent_annotation_id: child.annotation_id,
    });
    expect(next[1]).toEqual({
      ...child,
      start_step: 280,
      end_step: 500,
      parent_annotation_id: null,
      path: [child.tag_id],
    });
    expectValid(next);
  });

  it("automatically parents partial overlaps by duration, regardless of creation order", () => {
    const short = {
      ...child,
      parent_annotation_id: null,
      path: [child.tag_id],
      start_step: 15,
      end_step: 25,
    };
    const long = {
      ...grandchild,
      parent_annotation_id: null,
      path: [grandchild.tag_id],
      start_step: 0,
      end_step: 20,
    };
    const next = reconcileTagHierarchy([short, long]);
    expect(next[0]).toMatchObject({
      parent_annotation_id: long.annotation_id,
      path: [long.tag_id, short.tag_id],
    });
    expect(next[1]).toMatchObject({ parent_annotation_id: null });
    expect(reconcileTagHierarchy([long, short])).toEqual([next[1], next[0]]);
    expectValid(next);
  });

  it("keeps disjoint and touching intervals at the root and removes stale parent links", () => {
    const next = updateIntervalTag(
      [visualTag, child, grandchild],
      child.annotation_id,
      {
        start_step: visualTag.end_step,
        end_step: visualTag.end_step + 100,
      },
    );
    expect(next[1]).toMatchObject({
      parent_annotation_id: null,
      path: [child.tag_id],
    });
    expect(next[2]).toMatchObject({
      parent_annotation_id: visualTag.annotation_id,
      path: [...visualTag.path, grandchild.tag_id],
    });
    expectValid(next);
  });

  it("keeps equal-length overlapping intervals as peers without cycles", () => {
    const left = { ...child, start_step: 0, end_step: 100 };
    const right = { ...grandchild, start_step: 50, end_step: 150 };
    const next = reconcileTagHierarchy([left, right]);
    expect(next.map((tag) => tag.parent_annotation_id)).toEqual([null, null]);
    expectValid(next);
  });

  it("deletes only the selected parent, preserving children and their time ranges", () => {
    const next = removeIntervalTag(
      [grandchild, visualTag, child],
      visualTag.annotation_id,
    );
    expect(next).toHaveLength(2);
    expect(next[1]).toEqual({
      ...child,
      parent_annotation_id: null,
      path: [child.tag_id],
    });
    expect(next[0]).toEqual({
      ...grandchild,
      path: [child.tag_id, grandchild.tag_id],
    });
    expectValid(next);
  });

  it("promotes a deleted middle node's child to its grandparent", () => {
    const next = removeIntervalTag(
      [visualTag, child, grandchild],
      child.annotation_id,
    );
    expect(next[0]).toBe(visualTag);
    expect(next[1]).toEqual({
      ...grandchild,
      parent_annotation_id: visualTag.annotation_id,
      path: [...visualTag.path, grandchild.tag_id],
    });
    expectValid(next);
  });
});
