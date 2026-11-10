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
  fill_initial_action: "首条动作前用关节状态补齐",
  fill_action_from_next_frame: "缺动作时取下一训练帧的关节状态",
  forward_fill_hands: "手部空值沿用上一条指令",
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
  fill_initial_action: {},
  fill_action_from_next_frame: {},
  forward_fill_hands: {},
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
function axes(cfg: McapConfig, role: "action" | "state") {
  const names =
    role === "action" ? cfg.action_joint_names : cfg.state_joint_names;
  return (names.length ? names : cfg.joint_names).filter((n) => n.trim());
}
export function sample(kind: Kind, cfg: McapConfig) {
  if (!cfg.normalization) {
    const action = axes(cfg, "action"),
      state = axes(cfg, "state");
    const common = action.filter((n) => state.includes(n));
    const zero =
      state.find((n) => !action.includes(n)) ?? state.at(-1) ?? action.at(-1);
    if (kind === "zero_joints") return pretty({ axes: zero ? [zero] : [] });
    if (kind === "action_from_state")
      return pretty({
        axes: [common.find((n) => n !== zero) ?? common[0]].filter(
          (n) => n !== undefined,
        ),
        match: "at_or_after",
      });
    if (kind === "fill_position")
      return pretty({
        missing_position_axes: common.slice(0, 1),
        nan_position_axes: [],
        max_state_delta_ms: 20,
      });
  }
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
      (!saved && !!cfg.normalization && kind === "fill_position") ||
      (saved?.version === 1 &&
        kind === "generate_action" &&
        saved.missing_action_topic === "copy_state");
    return { kind, enabled, text: sample(kind, cfg) };
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
      op.enabled &&
      op.kind !== "zero_joints" &&
      group.find((n) => !allowed.includes(n));
    if (unknown) throw new Error("当前方案没有动作轴：" + unknown);
  }
  if (op.enabled && op.kind === "forward_fill_hands") {
    if (!["action.hand.left", "action.hand.right"].some((key) => cfg.numeric_sources?.[key]))
      throw new Error("请先在 MCAP 来源中映射左手或右手指令。");
    return op;
  }
  if (op.enabled) {
    if (!axes(cfg, "action").length || !axes(cfg, "state").length)
      throw new Error("请先在数据定义中填写状态和动作的关节名称。");
    const source = axes(cfg, "state");
    const fromState =
      op.kind === "action_from_state"
        ? op.axes
        : op.kind === "fill_position"
          ? [...(op.missing_position_axes ?? []), ...op.nan_position_axes]
          : [];
    if (
      op.kind === "fill_position" &&
      op.missing_position_axes === null &&
      !cfg.normalization
    )
      throw new Error("请填写需要补值的关节，或使用空数组 []。");
    const missing = fromState.find((n) => !source.includes(n));
    if (missing) throw new Error("当前方案没有对应状态轴：" + missing);
  }
  return op;
}
