import { Alert, Button, Input, Modal, Space } from "antd";
import { useState } from "react";
import { postprocessingRulesSchema, type SchemeDocument } from "./contracts";
import styles from "./definition.module.css";

const original = {
  version: 1,
  missing_position_axes: ["leg_1", "leg_2", "zhi"],
  nan_position_axes: [],
  max_state_delta_ms: 20,
  missing_action_topic: "reject",
} as const;

export function PostprocessingRules({
  document,
  onChange,
}: {
  document: SchemeDocument;
  onChange: (next: SchemeDocument) => void;
}) {
  const adapter = document.adapters.find((a) => a.source_format === "MCAP");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const rules = adapter?.configuration.postprocessing_rules ?? original;
  const available = !!adapter?.configuration.normalization;
  function apply() {
    try {
      const parsed = postprocessingRulesSchema.parse(JSON.parse(text));
      const allowed = adapter?.configuration.action_joint_names ?? [];
      for (const axes of [
        parsed.missing_position_axes,
        parsed.nan_position_axes,
      ]) {
        if (
          new Set(axes).size !== axes.length ||
          axes.some((axis) => !allowed.includes(axis))
        ) {
          throw new Error("请填写动作轴列表中的关节名，同一列表内不能重复。");
        }
      }
      onChange({
        ...document,
        adapters: document.adapters.map((a) =>
          a === adapter && a.source_format === "MCAP"
            ? {
                ...a,
                configuration: {
                  ...a.configuration,
                  postprocessing_rules: parsed,
                },
              }
            : a,
        ),
      });
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "规则格式无效");
    }
  }

  return (
    <section className={styles.card} aria-label="数据后处理规则">
      <h3>数据后处理规则</h3>
      {available ? (
        <>
          <p>
            先按规则处理
            MCAP，再对处理结果质检。补值来源与时间差会保存在处理记录中。
          </p>
          <p>
            缺失关节补值：{rules.missing_position_axes.join("、") || "关闭"}
            。NaN 补值：{rules.nan_position_axes.join("、") || "关闭"}
            。时间容差：{rules.max_state_delta_ms} 毫秒。
          </p>
          <p>
            整个主动作话题没有消息时：
            {rules.missing_action_topic === "copy_state"
              ? "按每条状态消息生成动作帧"
              : "报错"}
            。
          </p>
          <Button
            onClick={() => {
              setText(JSON.stringify(rules, null, 2));
              setError("");
              setEditing(true);
            }}
          >
            编辑后处理规则
          </Button>
          <Modal
            open={editing}
            title="编辑数据后处理规则"
            onCancel={() => setEditing(false)}
            onOk={apply}
            okText="应用到草稿"
            cancelText="取消"
            width={700}
          >
            <p>
              可直接编辑或导入 JSON。missing_position_axes 和 nan_position_axes
              分别指定关节缺失、位置为 NaN
              时允许从最近状态补值的轴。max_state_delta_ms 范围为大于 0 至 1000
              毫秒。
            </p>
            <p>
              missing_action_topic 设为 copy_state
              时，仅在主动作话题完全没有消息时生成动作；设为 reject
              时报告缺失。独立手指话题仍按方案校验。
            </p>
            <p>
              可选动作轴：{adapter?.configuration.action_joint_names.join("、")}
            </p>
            <Space wrap>
              <Button
                onClick={() => {
                  setText(
                    JSON.stringify(
                      {
                        ...original,
                        nan_position_axes: original.missing_position_axes,
                        missing_action_topic: "copy_state",
                      },
                      null,
                      2,
                    ),
                  );
                  setError("");
                }}
              >
                载入新录制方式示例
              </Button>
              <label>
                导入规则文件{" "}
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={async (e) => {
                    const file = e.currentTarget.files?.[0];
                    e.currentTarget.value = "";
                    if (!file) return;
                    if (file.size > 65536) {
                      setError("规则文件不能超过 64 KiB。");
                      return;
                    }
                    try {
                      setText(await file.text());
                      setError("");
                    } catch {
                      setError("规则文件读取失败。");
                    }
                  }}
                />
              </label>
            </Space>
            <Input.TextArea
              aria-label="后处理规则 JSON"
              value={text}
              rows={12}
              onChange={(e) => {
                setText(e.target.value);
                setError("");
              }}
            />
            {error && (
              <Alert type="error" title="规则未应用" description={error} />
            )}
          </Modal>
        </>
      ) : (
        <p>状态补动作规则适用于启用了 HC TJ 17 轴归一化的 MCAP 方案。</p>
      )}
    </section>
  );
}
