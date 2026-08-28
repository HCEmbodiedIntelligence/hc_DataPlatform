import { describe, expect, it } from "vitest";
import { buildTagSchemaIndex, evaluateAnnotationTags } from "./tag-validation";
import type {
  RuntimeAnnotationTag,
  RuntimeTagSchemaVersion,
} from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualSchema,
  visualTag,
} from "./testing/annotation-fixture";

describe("P08 Tag hierarchy and review checks", () => {
  it("passes the six review dimensions for a valid generated-contract Tag", () => {
    const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
    const checks = evaluateAnnotationTags({
      task: bundle.task,
      schema: bundle.schema,
      tags: [visualTag],
    });
    expect(checks.map((check) => check.kind)).toEqual([
      "HIERARCHY",
      "BOUNDARY",
      "REQUIRED_ATTRIBUTES",
      "MUTUAL_EXCLUSION",
      "OBJECT_RELATIONS",
      "SCHEMA_VERSION",
    ]);
    expect(checks.every((check) => check.status === "PASS")).toBe(true);
  });

  it.each([
    ["missing-required", "REQUIRED_ATTRIBUTES"],
    ["mutual-exclusion", "MUTUAL_EXCLUSION"],
    ["boundary", "BOUNDARY"],
  ] as const)(
    "blocks %s with an explainable %s result",
    (invalid, expectedKind) => {
      const bundle = createVisualAnnotationBundle({
        mode: "tag-review",
        invalid,
      });
      const tags = bundle.history.revisions.at(-1)?.tags ?? [];
      const checks = evaluateAnnotationTags({
        task: bundle.task,
        schema: bundle.schema,
        tags,
      });
      expect(checks.find((check) => check.kind === expectedKind)).toMatchObject(
        { status: "FAIL" },
      );
    },
  );

  it("detects a schema cycle without imposing a product depth cap", () => {
    const cycle = createVisualAnnotationBundle({ invalid: "cycle" });
    expect(buildTagSchemaIndex(cycle.schema).errors.join(" ")).toMatch(/循环/u);

    const depth = 128;
    const nodes: RuntimeTagSchemaVersion["document"]["nodes"] = Array.from(
      { length: depth },
      (_, index) => ({
        tag_id: `depth-${index}`,
        code: `depth_${index}`,
        display_name: `层级 ${index + 1}`,
        parent_tag_id: index === 0 ? null : `depth-${index - 1}`,
        attributes: [],
      }),
    );
    const schema: RuntimeTagSchemaVersion = {
      ...visualSchema,
      schema_id: "deep-schema",
      document: { nodes, mutual_exclusions: [], object_relations: [] },
    };
    const index = buildTagSchemaIndex(schema);
    expect(index.errors).toEqual([]);
    expect(index.byId.get(`depth-${depth - 1}`)?.path).toHaveLength(depth);
  });

  it("treats adjacent half-open mutually exclusive intervals as non-overlapping", () => {
    const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
    const adjacent: RuntimeAnnotationTag = {
      ...visualTag,
      annotation_id: "annotation-adjacent-failure",
      tag_id: "grasp-failure",
      path: ["operation-stage", "grasp-action", "grasp-failure"],
      start_step: visualTag.end_step,
      end_step: visualTag.end_step + 10,
      attributes: {
        station: "装配 A",
        method: "平行夹爪",
        failure_reason: "边界样例",
      },
    };
    const check = evaluateAnnotationTags({
      task: bundle.task,
      schema: bundle.schema,
      tags: [visualTag, adjacent],
    }).find((item) => item.kind === "MUTUAL_EXCLUSION");
    expect(check?.status).toBe("PASS");
  });

  it("allows a manual child interval to extend beyond and cross its semantic parent", () => {
    const bundle = createVisualAnnotationBundle({ mode: "tag-review" });
    const basketball: RuntimeAnnotationTag = {
      annotation_id: "manual-basketball",
      tag_id: "manual-node-basketball",
      label: "打篮球",
      parent_annotation_id: null,
      path: ["manual-node-basketball"],
      start_step: 60,
      end_step: 120,
      attributes: {},
      relations: [],
      subject: null,
    };
    const dribbling: RuntimeAnnotationTag = {
      annotation_id: "manual-dribbling",
      tag_id: "manual-node-dribbling",
      label: "运球",
      parent_annotation_id: basketball.annotation_id,
      path: [...basketball.path, "manual-node-dribbling"],
      start_step: 30,
      end_step: 180,
      attributes: {},
      relations: [],
      subject: null,
    };

    const checks = evaluateAnnotationTags({
      task: bundle.task,
      schema: bundle.schema,
      tags: [basketball, dribbling],
    });

    expect(checks.every((check) => check.status === "PASS")).toBe(true);
  });
});
