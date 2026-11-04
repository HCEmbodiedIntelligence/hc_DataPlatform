import {
  processingOperationSchema,
  type McapConfig,
  type ProcessingOperation,
} from "./contracts";
import zeroExample from "./rule-examples/zero_joints.json";
import copyExample from "./rule-examples/action_from_state.json";

export type Kind = ProcessingOperation["kind"];
export type RuleRow = { kind: Kind; enabled: boolean; text: string };
export const labels: Record<Kind, string> = {
  zero_joints: "关节置零",
  action_from_state: "动作跟随状态",
  fill_position: "缺失位置补值",
  generate_action: "缺少动作话题时生成动作",
};
const examples = {
  zero_joints: zeroExample,
  action_from_state: copyExample,
  fill_position: {
    missing_position_axes: null,
    nan_position_axes: [],
    max_state_delta_ms: 20,
  },
  generate_action: { source: "state" },
};
export const kinds = Object.keys(labels) as Kind[];
function pretty(v: object) {
  return (
    "{\n" +
    Object.entries(v)
      .map(
        ([key, val]) =>
          "  " +
          JSON.stringify(key) +
          ": " +
          (Array.isArray(val)
            ? "[" + val.map((x) => JSON.stringify(x)).join(", ") + "]"
            : JSON.stringify(val)),
      )
      .join(",\n") +
    "\n}"
  );
}
export function sample(kind: Kind) {
  return pretty(examples[kind]);
}

export function initialRows(cfg: McapConfig): RuleRow[] {
  const saved = cfg.postprocessing_rules;
  return kinds.map((kind) => {
    const op =
      saved?.version === 2 ? saved.rules.find((x) => x.kind === kind) : null;
    if (op) {
      const { kind, enabled, ...params } = op;
      return { kind, enabled, text: pretty(params) };
    }
    if (saved?.version === 1 && kind === "fill_position") {
      const { missing_position_axes, nan_position_axes, max_state_delta_ms } =
        saved;
      return {
        kind,
        enabled: !!(missing_position_axes.length || nan_position_axes.length),
        text: pretty({
          missing_position_axes,
          nan_position_axes,
          max_state_delta_ms,
        }),
      };
    }
    const enabled =
      (!saved && kind === "fill_position") ||
      (saved?.version === 1 &&
        kind === "generate_action" &&
        saved.missing_action_topic === "copy_state");
    return { kind, enabled, text: sample(kind) };
  });
}

export function parseRow(row: RuleRow, cfg: McapConfig): ProcessingOperation {
  let params;
  try {
    params = JSON.parse(row.text);
  } catch {
    throw new Error("JSON 格式有误，请检查引号、逗号和括号。");
  }
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    "kind" in params ||
    "enabled" in params
  )
    throw new Error("这里只填写当前规则的参数，可载入示例查看格式。");
  const result = processingOperationSchema.safeParse({
    ...params,
    kind: row.kind,
    enabled: row.enabled,
  });
  if (!result.success) {
    const issue = result.error.issues[0];
    const key =
      issue?.path.join(".") ||
      (issue?.code === "unrecognized_keys" ? issue.keys.join("、") : "");
    throw new Error(
      key ? "请检查字段：" + key : "参数格式不符，可载入示例查看格式。",
    );
  }
  const op = result.data;
  const groups =
    "axes" in op
      ? [op.axes]
      : op.kind === "fill_position"
        ? [op.missing_position_axes ?? [], op.nan_position_axes]
        : [];
  if ("axes" in op && !op.axes.length && op.enabled)
    throw new Error("请至少填写一个关节。");
  const allowed = cfg.action_joint_names.length
    ? cfg.action_joint_names
    : cfg.joint_names;
  for (const group of groups) {
    if (new Set(group).size !== group.length)
      throw new Error("同一组内不能重复填写关节。");
    const unknown =
      op.kind !== "zero_joints" && group.find((n) => !allowed.includes(n));
    if (unknown) throw new Error("当前方案没有动作轴：" + unknown);
  }
  return op;
}
