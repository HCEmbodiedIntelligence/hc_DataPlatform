import { Checkbox, Form, InputNumber } from "antd";
import styles from "./definition.module.css";

export type Values = Record<string, number | boolean | null>;
const labels: Record<string, string> = {
  minimum_frequency_ratio: "最小频率比例",
  minimum_coverage_ratio: "最小时间覆盖比例",
  maximum_gap_ns: "最大时间间隔（毫秒）",
  maximum_consecutive_missing: "最多连续缺帧数",
  maximum_duplicate_timestamps: "最多重复时间戳数",
  maximum_backward_timestamps: "最多倒退时间戳数",
  minimum_ratio: "最小完整帧比例",
  maximum_p95_offset_ns: "95% 分位同步误差（毫秒）",
  black_luma_threshold: "黑帧亮度界限",
  maximum_black_frame_ratio: "最大黑帧比例",
  maximum_repeated_frame_ratio: "最大重复帧比例",
  maximum_corrupt_frame_ratio: "最大损坏帧比例",
  minimum_observation_count: "最少动作记录数",
  maximum_jump: "最大相邻动作跳变量",
  dimension_mismatch_is_reject: "动作维数不匹配时拒绝",
  minimum_point_count: "最少点数",
  maximum_point_count: "最多点数",
  maximum_empty_ratio: "最大空点云比例",
  maximum_abnormal_count_ratio: "最大异常点数比例",
};

export function parameterLabel(key: string) {
  if (labels[key]) return labels[key];
  const kind = key.endsWith("_risk") ? "提示" : "拒绝";
  const base = key.replace(/_(risk|reject)$/, "");
  return (labels[base] ?? base) + " · " + kind;
}

export function parameterScale(key: string) {
  return key.includes("_ns_") ? 1000000 : 1;
}

export function QualityParameters({
  defaults,
  values,
  onChange,
  prefix = "",
}: {
  defaults: Values;
  values: Values;
  onChange: (next: Values) => void;
  prefix?: string;
}) {
  return (
    <div className={styles.dimensions}>
      {Object.entries(defaults).map(([key, fallback]) => {
        const label = prefix + parameterLabel(key);
        const scale = parameterScale(key);
        const current = values[key];
        return (
          <Form.Item key={key} label={label}>
            {typeof fallback === "boolean" ? (
              <Checkbox
                aria-label={label}
                checked={Boolean(current)}
                onChange={(e) =>
                  onChange({ ...values, [key]: e.target.checked })
                }
              >
                启用
              </Checkbox>
            ) : (
              <InputNumber
                aria-label={label}
                value={typeof current === "number" ? current / scale : null}
                min={0}
                max={key.includes("ratio") ? 1 : undefined}
                placeholder={fallback === null ? "不限制" : "请输入阈值"}
                step={key.includes("ratio") ? 0.01 : 1}
                onChange={(next) =>
                  onChange({
                    ...values,
                    [key]: next === null ? null : next * scale,
                  })
                }
              />
            )}
          </Form.Item>
        );
      })}
    </div>
  );
}
