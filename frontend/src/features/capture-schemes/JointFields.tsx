import { Alert, Button, Input, Select, Space } from "antd";
import { ArrowUp, Trash2 } from "lucide-react";
import type { Feature } from "./contracts";
import styles from "./definition.module.css";

const units = [
  { value: "rad", label: "弧度（rad）" },
  { value: "deg", label: "角度（deg）" },
  { value: "m", label: "米（m）" },
  { value: "mm", label: "毫米（mm）" },
  { value: "1", label: "无量纲（1）" },
];
export function JointFields({
  name,
  feature: f,
  disabled,
  onChange,
}: {
  name: string;
  feature: Feature;
  disabled: boolean;
  onChange: (feature: Feature) => void;
}) {
  const names = Array.isArray(f.names) ? f.names : [];
  const uniform =
    f.units.length === names.length &&
    f.units.every((unit) => unit === f.units[0])
      ? f.units[0] || undefined
      : undefined;
  return (
    <>
      <div className={styles.jointSummary}>
        <strong>{names.length} 个关节</strong>
        <span>
          {uniform
            ? `单位：${units.find((unit) => unit.value === uniform)?.label ?? uniform}`
            : "单位：请逐轴确认"}
        </span>
      </div>
      {!disabled && (
        <Space wrap className={styles.bulkUnit}>
          <span>统一设置单位</span>
          <Select
            aria-label={`${name} 统一单位`}
            placeholder="选择后应用到本组全部关节"
            style={{ minWidth: 230 }}
            value={uniform}
            options={units}
            onChange={(unit) =>
              onChange({ ...f, units: names.map(() => unit) })
            }
          />
        </Space>
      )}
      <details className={styles.details}>
        <summary>
          {disabled ? "查看" : "查看与编辑"} {names.length} 个关节（按记录顺序）
        </summary>
        {!names.length && (
          <Alert type="info" title="添加关节或从样本读取关节列表" />
        )}
        <div className={styles.axisTable}>
          <div className={styles.axisHeader}>
            <span>序号</span>
            <span>关节名称</span>
            <span>单位</span>
            <span>顺序</span>
          </div>
          {names.map((axis, i) => (
            <div key={i} className={styles.axisRow}>
              <span>{i + 1}</span>
              <Input
                aria-label={`${name} 第${i + 1}轴名称`}
                disabled={disabled}
                placeholder="原始数据中的关节名称"
                value={axis}
                onChange={(e) => {
                  const next = [...names];
                  next[i] = e.target.value;
                  onChange({ ...f, names: next });
                }}
              />
              <Input
                aria-label={`${name} 第${i + 1}轴单位`}
                disabled={disabled}
                placeholder="rad / m"
                value={f.units[i] ?? ""}
                onChange={(e) => {
                  const next = [...f.units];
                  next[i] = e.target.value;
                  onChange({ ...f, units: next });
                }}
              />
              <Space size={2}>
                <Button
                  aria-label={`${name} 第${i + 1}轴上移`}
                  title="上移"
                  disabled={disabled || i === 0}
                  icon={<ArrowUp size={14} />}
                  onClick={() => {
                    const next = [...names],
                      nextUnits = [...f.units];
                    [next[i - 1], next[i]] = [next[i]!, next[i - 1]!];
                    [nextUnits[i - 1], nextUnits[i]] = [
                      nextUnits[i]!,
                      nextUnits[i - 1]!,
                    ];
                    onChange({ ...f, names: next, units: nextUnits });
                  }}
                />
                {!disabled && (
                  <Button
                    aria-label={`${name} 第${i + 1}轴删除`}
                    title="删除关节"
                    disabled={names.length <= 1}
                    icon={<Trash2 size={14} />}
                    onClick={() =>
                      onChange({
                        ...f,
                        names: names.filter((_, n) => n !== i),
                        units: f.units.filter((_, n) => n !== i),
                        shape: [names.length - 1],
                      })
                    }
                  />
                )}
              </Space>
            </div>
          ))}
        </div>
        {!disabled && (
          <Button
            onClick={() =>
              onChange({
                ...f,
                names: [...names, ""],
                units: [...f.units, ""],
                shape: [names.length + 1],
              })
            }
          >
            添加关节
          </Button>
        )}
      </details>
    </>
  );
}
