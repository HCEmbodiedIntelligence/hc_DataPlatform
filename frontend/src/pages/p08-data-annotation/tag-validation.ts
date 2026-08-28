import type {
  RuntimeAnnotationTag,
  RuntimeAnnotationTask,
  RuntimeReviewCheckKind,
  RuntimeTagSchemaVersion,
} from "./runtime-annotation-adapter";

export interface TagSchemaTreeRow {
  readonly node: RuntimeTagSchemaVersion["document"]["nodes"][number];
  readonly depth: number;
  readonly path: readonly string[];
  readonly displayPath: readonly string[];
  readonly hasChildren: boolean;
}

export interface TagSchemaIndex {
  readonly rows: readonly TagSchemaTreeRow[];
  readonly byId: ReadonlyMap<string, TagSchemaTreeRow>;
  readonly errors: readonly string[];
}

export interface TagReviewCheckResult {
  readonly kind: RuntimeReviewCheckKind;
  readonly label: string;
  readonly status: "PASS" | "FAIL";
  readonly evidence: string;
}

const checkLabels: Readonly<Record<RuntimeReviewCheckKind, string>> = {
  HIERARCHY: "多级区间层级正确",
  BOUNDARY: "标签区间边界准确",
  REQUIRED_ATTRIBUTES: "必填属性完整",
  MUTUAL_EXCLUSION: "标签互斥 / 冲突",
  OBJECT_RELATIONS: "动作与对象关系合理",
  SCHEMA_VERSION: "标签结构版本匹配",
};

function nonEmpty(value: unknown): boolean {
  return (
    value !== null &&
    value !== undefined &&
    !(typeof value === "string" && value.trim() === "")
  );
}

function attributeMatches(
  value: unknown,
  definition: RuntimeTagSchemaVersion["document"]["nodes"][number]["attributes"][number],
): boolean {
  if (!nonEmpty(value)) return !definition.required;
  if (definition.value_type === "STRING") return typeof value === "string";
  if (definition.value_type === "INTEGER")
    return typeof value === "number" && Number.isInteger(value);
  if (definition.value_type === "NUMBER")
    return typeof value === "number" && Number.isFinite(value);
  if (definition.value_type === "BOOLEAN") return typeof value === "boolean";
  if (definition.value_type === "ENUM") {
    return typeof value === "string" && definition.enum_values.includes(value);
  }
  return false;
}

export function buildTagSchemaIndex(
  schema: RuntimeTagSchemaVersion,
): TagSchemaIndex {
  const nodes = schema.document.nodes;
  const nodesById = new Map<
    string,
    RuntimeTagSchemaVersion["document"]["nodes"][number]
  >();
  const children = new Map<
    string | null,
    RuntimeTagSchemaVersion["document"]["nodes"][number][]
  >();
  const errors: string[] = [];
  for (const node of nodes) {
    if (nodesById.has(node.tag_id)) errors.push(`Tag ID 重复：${node.tag_id}`);
    else nodesById.set(node.tag_id, node);
  }
  for (const node of nodes) {
    const parentId = node.parent_tag_id ?? null;
    if (parentId !== null && !nodesById.has(parentId)) {
      errors.push(`Tag ${node.tag_id} 引用了不存在的父级 ${parentId}`);
      continue;
    }
    const siblings = children.get(parentId) ?? [];
    siblings.push(node);
    children.set(parentId, siblings);
  }
  const pathCache = new Map<string, readonly string[]>();
  const displayCache = new Map<string, readonly string[]>();
  const resolvePath = (
    nodeId: string,
  ): { path: readonly string[]; displayPath: readonly string[] } | null => {
    const cached = pathCache.get(nodeId);
    if (cached)
      return { path: cached, displayPath: displayCache.get(nodeId) ?? cached };
    const chain: RuntimeTagSchemaVersion["document"]["nodes"][number][] = [];
    const seen = new Set<string>();
    let current = nodesById.get(nodeId);
    while (current) {
      if (seen.has(current.tag_id)) {
        errors.push(
          `标签结构存在循环：${[...seen, current.tag_id].join(" → ")}`,
        );
        return null;
      }
      seen.add(current.tag_id);
      chain.push(current);
      current = current.parent_tag_id
        ? nodesById.get(current.parent_tag_id)
        : undefined;
    }
    const ordered = chain.reverse();
    const path = ordered.map((node) => node.tag_id);
    const displayPath = ordered.map((node) => node.display_name);
    pathCache.set(nodeId, path);
    displayCache.set(nodeId, displayPath);
    return { path, displayPath };
  };
  const rows: TagSchemaTreeRow[] = [];
  const visit = (parentId: string | null, depth: number) => {
    const siblings = [...(children.get(parentId) ?? [])].sort((left, right) =>
      left.display_name.localeCompare(right.display_name, "zh-CN"),
    );
    for (const node of siblings) {
      const resolved = resolvePath(node.tag_id);
      if (resolved) {
        rows.push({
          node,
          depth,
          path: resolved.path,
          displayPath: resolved.displayPath,
          hasChildren: (children.get(node.tag_id)?.length ?? 0) > 0,
        });
      }
      visit(node.tag_id, depth + 1);
    }
  };
  visit(null, 0);
  for (const node of nodes) resolvePath(node.tag_id);
  const byId = new Map(rows.map((row) => [row.node.tag_id, row]));
  return { rows, byId, errors: [...new Set(errors)] };
}

export function inheritedAttributes(
  row: TagSchemaTreeRow,
  index: TagSchemaIndex,
): readonly RuntimeTagSchemaVersion["document"]["nodes"][number]["attributes"][number][] {
  const definitions = new Map<
    string,
    RuntimeTagSchemaVersion["document"]["nodes"][number]["attributes"][number]
  >();
  for (const tagId of row.path) {
    for (const attribute of index.byId.get(tagId)?.node.attributes ?? [])
      definitions.set(attribute.key, attribute);
  }
  return [...definitions.values()];
}

function intervalsOverlap(
  left: RuntimeAnnotationTag,
  right: RuntimeAnnotationTag,
): boolean {
  return left.start_step < right.end_step && right.start_step < left.end_step;
}

export function evaluateAnnotationTags(input: {
  readonly task: RuntimeAnnotationTask;
  readonly schema: RuntimeTagSchemaVersion;
  readonly tags: readonly RuntimeAnnotationTag[];
}): readonly TagReviewCheckResult[] {
  const { task, schema, tags } = input;
  const index = buildTagSchemaIndex(schema);
  const hierarchyErrors = [...index.errors];
  const tagByAnnotationId = new Map(
    tags.map((tag) => [tag.annotation_id, tag]),
  );
  if (tagByAnnotationId.size !== tags.length)
    hierarchyErrors.push("同一修订中的 Tag 区间 ID 必须唯一");
  for (const tag of tags) {
    if (tag.label !== null && tag.label !== undefined) {
      if (!tag.label.trim() || tag.label !== tag.label.trim())
        hierarchyErrors.push(`${tag.annotation_id} 的手工标签名称无效`);
      const parent = tag.parent_annotation_id
        ? tagByAnnotationId.get(tag.parent_annotation_id)
        : undefined;
      if (tag.parent_annotation_id && !parent) {
        hierarchyErrors.push(`${tag.annotation_id} 引用了不存在的父区间`);
        continue;
      }
      const expectedPath = parent ? [...parent.path, tag.tag_id] : [tag.tag_id];
      if (tag.path.join("\u0000") !== expectedPath.join("\u0000"))
        hierarchyErrors.push(`${tag.annotation_id} 的路径与区间父子关系不一致`);
      continue;
    }
    const row = index.byId.get(tag.tag_id);
    if (!row)
      hierarchyErrors.push(`${tag.annotation_id} 使用未知 Tag ${tag.tag_id}`);
    else if (tag.path.join("\u0000") !== row.path.join("\u0000")) {
      hierarchyErrors.push(
        `${tag.annotation_id} 路径应为 ${row.displayPath.join(" / ")}`,
      );
    }
  }
  for (const tag of tags) {
    if (tag.label === null || tag.label === undefined) continue;
    const seen = new Set<string>();
    let current: RuntimeAnnotationTag | undefined = tag;
    while (current?.label !== null && current?.label !== undefined) {
      if (seen.has(current.annotation_id)) {
        hierarchyErrors.push("手工 Tag 区间层级存在循环");
        break;
      }
      seen.add(current.annotation_id);
      current = current.parent_annotation_id
        ? tagByAnnotationId.get(current.parent_annotation_id)
        : undefined;
    }
  }

  const boundaryErrors: string[] = [];
  for (const tag of tags) {
    if (
      !Number.isInteger(tag.start_step) ||
      !Number.isInteger(tag.end_step) ||
      tag.start_step < 0 ||
      tag.start_step >= tag.end_step
    ) {
      boundaryErrors.push(`${tag.annotation_id} 不是有效半开区间`);
    } else if (
      task.base_step_count === null ||
      task.base_step_count === undefined
    ) {
      boundaryErrors.push("任务未返回 base_step_count，无法确认上边界");
    } else if (tag.end_step > task.base_step_count) {
      boundaryErrors.push(
        `${tag.annotation_id} 结束步 ${tag.end_step} 超出 ${task.base_step_count}`,
      );
    }
  }

  const attributeErrors: string[] = [];
  for (const tag of tags) {
    if (tag.label !== null && tag.label !== undefined) continue;
    const row = index.byId.get(tag.tag_id);
    if (!row) continue;
    for (const definition of inheritedAttributes(row, index)) {
      const value = tag.attributes?.[definition.key];
      if (!attributeMatches(value, definition)) {
        attributeErrors.push(
          `${row.node.display_name}：${definition.display_name} 缺失或类型不符`,
        );
      }
    }
  }

  const mutualErrors: string[] = [];
  for (const constraint of schema.document.mutual_exclusions) {
    const candidates = tags.filter((tag) =>
      constraint.tag_ids.includes(tag.tag_id),
    );
    for (let left = 0; left < candidates.length; left += 1) {
      for (let right = left + 1; right < candidates.length; right += 1) {
        const leftTag = candidates[left];
        const rightTag = candidates[right];
        if (
          leftTag &&
          rightTag &&
          leftTag.tag_id !== rightTag.tag_id &&
          intervalsOverlap(leftTag, rightTag)
        ) {
          mutualErrors.push(
            `${constraint.constraint_id}：${leftTag.annotation_id} 与 ${rightTag.annotation_id} 区间重叠`,
          );
        }
      }
    }
  }

  const relationErrors: string[] = [];
  for (const tag of tags) {
    if (tag.label !== null && tag.label !== undefined) continue;
    for (const constraint of schema.document.object_relations) {
      if (
        !constraint.required ||
        !constraint.source_tag_ids.includes(tag.tag_id)
      )
        continue;
      const matched = tag.relations.some(
        (relation) =>
          relation.relation_type === constraint.relation_type &&
          constraint.target_object_types.includes(
            relation.target.object_type,
          ) &&
          relation.target.object_id.trim() !== "",
      );
      if (!matched)
        relationErrors.push(
          `${tag.annotation_id} 缺少 ${constraint.relation_type} 对象关系`,
        );
    }
  }

  const schemaErrors =
    schema.project_id === task.project_id &&
    schema.schema_id === task.tag_schema_id &&
    schema.version === task.tag_schema_version &&
    schema.status === "PUBLISHED"
      ? []
      : [
          `任务要求 ${task.tag_schema_id} v${task.tag_schema_version} 的已发布版本`,
        ];

  const result = (
    [
      [
        "HIERARCHY",
        hierarchyErrors,
        `已核对 ${tags.length} 个 Tag 的区间父子关系与完整路径`,
      ],
      [
        "BOUNDARY",
        boundaryErrors,
        `区间使用 [start_step, end_step)，基线 ${task.base_step_count ?? "未知"} 步`,
      ],
      [
        "REQUIRED_ATTRIBUTES",
        attributeErrors,
        "已按完整祖先路径继承并核对必填属性",
      ],
      [
        "MUTUAL_EXCLUSION",
        mutualErrors,
        `已执行 ${schema.document.mutual_exclusions.length} 组互斥约束`,
      ],
      [
        "OBJECT_RELATIONS",
        relationErrors,
        `已执行 ${schema.document.object_relations.length} 条对象关系约束`,
      ],
      [
        "SCHEMA_VERSION",
        schemaErrors,
        `${schema.name} · ${schema.schema_id} v${schema.version} · ${schema.status}`,
      ],
    ] as const
  ).map(([kind, errors, successEvidence]) => ({
    kind,
    label: checkLabels[kind],
    status: errors.length ? ("FAIL" as const) : ("PASS" as const),
    evidence: errors.length ? errors.join("；") : successEvidence,
  }));
  return result;
}

export function emptyAttributesForRow(
  row: TagSchemaTreeRow,
  index: TagSchemaIndex,
): RuntimeAnnotationTag["attributes"] {
  return Object.fromEntries(
    inheritedAttributes(row, index).map((definition) => {
      if (definition.value_type === "BOOLEAN") return [definition.key, false];
      if (
        definition.value_type === "INTEGER" ||
        definition.value_type === "NUMBER"
      )
        return [definition.key, 0];
      return [
        definition.key,
        definition.value_type === "ENUM"
          ? (definition.enum_values[0] ?? "")
          : "",
      ];
    }),
  );
}
