import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
} from "antd";
import { useState } from "react";
import { Activity, Camera, ListTree } from "lucide-react";
import type { Feature, SchemeDocument } from "./contracts";
import { newFeature } from "./editor-model";
import { JointFields } from "./JointFields";
import styles from "./definition.module.css";

const roles: { value: Feature["role"]; label: string }[] = [
  { value: "state", label: "机器人状态（实际反馈）" },
  { value: "action", label: "控制动作（发出的指令）" },
  { value: "rgb", label: "彩色相机" },
  { value: "depth", label: "深度相机" },
  { value: "numeric", label: "辅助数值" },
  { value: "validity", label: "数据有效性" },
  { value: "timestamp", label: "时间信息" },
];
const labels: Record<string, string> = {
  "observation.state": "机器人状态",
  action: "控制动作",
  "observation.left_arm.ee_pose": "左臂末端位置与姿态",
  "observation.right_arm.ee_pose": "右臂末端位置与姿态",
  "action.left_arm.ee_pose": "左臂目标位置与姿态",
  "action.right_arm.ee_pose": "右臂目标位置与姿态",
  "observation.base.pose": "底盘位置与姿态",
  "observation.headset.pose": "头显位置与姿态",
  "observation.source_time": "各路数据的采样时间",
  "observation.recording_time": "录制时间",
  "observation.source_available": "各路数据是否采集到",
  "observation.source_valid": "各路数据是否有效",
  "observation.source_age": "各路数据的延迟",
  "observation.images.observation_cameras_head_color": "头部彩色相机",
};
function label(key: string, feature: Feature) {
  return (
    labels[key] ??
    (feature.role === "rgb" || feature.role === "depth"
      ? `${feature.role === "rgb" ? "彩色相机" : "深度相机"} · ${key.replace(/^observation.images\./, "")}`
      : key)
  );
}
function Field({
  name,
  feature: f,
  features,
  disabled,
  onChange,
  onRemove,
}: {
  name: string;
  feature: Feature;
  features: Record<string, Feature>;
  disabled: boolean;
  onChange: (feature: Feature) => void;
  onRemove: () => void;
}) {
  const joints = f.role === "state" || f.role === "action";
  const camera = f.role === "rgb" || f.role === "depth";
  const cameraDimension = (dimension: string, fallback: number) =>
    Array.isArray(f.names) && f.names.includes(dimension)
      ? f.names.indexOf(dimension)
      : fallback;
  return (
    <section className={styles.card} aria-label={`${label(name, f)}配置`}>
      <div className={styles.cardHeader}>
        <div>
          <h4>{label(name, f)}</h4>
          <code>{name}</code>
        </div>
        <Checkbox
          disabled={disabled}
          checked={f.required}
          onChange={(e) => onChange({ ...f, required: e.target.checked })}
        >
          必须采集
        </Checkbox>
      </div>
      {joints && (
        <>
          <p className={styles.hint}>
            {f.role === "state"
              ? "记录机器人实际反馈的关节位置。"
              : "记录控制端实际发送的关节指令。"}
            名称和顺序须与原始数据一致。
          </p>
          <JointFields
            name={name}
            feature={f}
            disabled={disabled}
            onChange={onChange}
          />
        </>
      )}
      {camera && (
        <div className={styles.dimensions}>
          {(
            [
              ["width", "图像宽度（像素）", 1],
              ["height", "图像高度（像素）", 0],
              ...(f.shape.length >= 3 ? [["channels", "通道数", 2]] : []),
            ] as [string, string, number][]
          ).map(([dimension, title, fallback]) => {
            const index = cameraDimension(dimension, fallback);
            return (
              <Form.Item key={dimension} label={title}>
                <InputNumber
                  aria-label={`${name} ${title}`}
                  disabled={disabled}
                  min={1}
                  precision={0}
                  value={f.shape[index]}
                  onChange={(value) => {
                    const shape = [...f.shape];
                    shape[index] = value ?? 0;
                    const media = { ...f.media };
                    if (`video.${dimension}` in media)
                      media[`video.${dimension}`] = value ?? 0;
                    onChange({ ...f, shape, media });
                  }}
                />
              </Form.Item>
            );
          })}
        </div>
      )}
      {!joints && !camera && (
        <p className={styles.hint}>
          {f.shape.join(" × ")} 个数值 · {f.dtype}
          {Array.isArray(f.names) ? ` · ${f.names.join("、")}` : ""}
        </p>
      )}
      {!f.required && (
        <p className={styles.hint}>
          允许不采集；缺失会单独记录，不视为真实零值。
        </p>
      )}
      <details className={styles.details}>
        <summary>{disabled ? "查看字段细节" : "高级设置"}</summary>
        <div className={styles.advanced}>
          <Form.Item label="数据角色">
            <Select
              aria-label={`${name} 数据角色`}
              disabled={disabled}
              value={f.role}
              options={roles}
              onChange={(role) => onChange({ ...f, role })}
            />
          </Form.Item>
          <Form.Item label="数据类型（与原始文件一致）">
            <Input
              aria-label={`${name} 数据类型`}
              disabled={disabled}
              value={f.dtype}
              onChange={(e) => onChange({ ...f, dtype: e.target.value })}
            />
          </Form.Item>
          {!joints && !camera && (
            <Form.Item label="数值数量（多维时用逗号分隔）">
              <Input
                aria-label={`${name} 维度`}
                disabled={disabled}
                value={f.shape.join(",")}
                onChange={(e) =>
                  onChange({
                    ...f,
                    shape: e.target.value.split(/[,，]/).map(Number),
                  })
                }
              />
            </Form.Item>
          )}
          <Form.Item
            label="有效性标记"
            extra="用于区分真实数据与缺失、过期数据。已有样本定义时保留原值。"
          >
            <Select
              aria-label={`${name} 有效性字段`}
              disabled={disabled}
              allowClear
              value={f.validity_feature ?? undefined}
              options={Object.entries(features)
                .filter(([, v]) => v.role === "validity" && v.required)
                .map(([key, v]) => ({ value: key, label: label(key, v) }))}
              onChange={(value) =>
                onChange({ ...f, validity_feature: value ?? null })
              }
            />
          </Form.Item>
          {!disabled && (
            <Button danger onClick={onRemove}>
              移除字段
            </Button>
          )}
        </div>
      </details>
    </section>
  );
}

export function DefinitionFields({
  document: doc,
  onChange,
  disabled = false,
}: {
  document: SchemeDocument;
  onChange: (doc: SchemeDocument) => void;
  disabled?: boolean;
}) {
  const features = doc.output.features;
  const entries = Object.entries(features);
  const joints = [
    ...entries.filter(([, f]) => f.role === "state"),
    ...entries.filter(([, f]) => f.role === "action"),
  ];
  const cameras = entries.filter(
    ([, f]) => f.role === "rgb" || f.role === "depth",
  );
  const auxiliary = entries.filter(
    ([, f]) => !["state", "action", "rgb", "depth"].includes(f.role),
  );
  const set = (name: string, feature: Feature) =>
    onChange({
      ...doc,
      output: { ...doc.output, features: { ...features, [name]: feature } },
    });
  const field = ([name, feature]: [string, Feature]) => (
    <Field
      key={name}
      name={name}
      feature={feature}
      features={features}
      disabled={disabled}
      onChange={(next) => set(name, next)}
      onRemove={() => {
        const next = { ...features };
        delete next[name];
        onChange({ ...doc, output: { ...doc.output, features: next } });
      }}
    />
  );
  const missingUnits = joints.some(
    ([, f]) =>
      f.units.length !== f.shape[0] || f.units.some((unit) => !unit.trim()),
  );
  return (
    <div className={styles.definition}>
      <div className={styles.overview}>
        <Form.Item
          label="采样频率（每秒多少帧）"
          extra={
            disabled
              ? "按原始频率处理，保留全部帧。"
              : "选填。留空将在样本验证时自动读取原始帧率，保留全部帧。"
          }
        >
          <InputNumber
            aria-label="实际原始帧率"
            disabled={disabled}
            min={0.01}
            max={1000}
            addonAfter="Hz"
            placeholder="从样本自动读取"
            value={doc.output.frequency_hz || null}
            onChange={(value) =>
              onChange({
                ...doc,
                output: { ...doc.output, frequency_hz: value },
              })
            }
          />
        </Form.Item>
        <Space wrap>
          <Tag>
            {joints.find(([, f]) => f.role === "state")?.[1].shape[0] ?? 0}{" "}
            个状态关节
          </Tag>
          <Tag>
            {joints.find(([, f]) => f.role === "action")?.[1].shape[0] ?? 0}{" "}
            个动作关节
          </Tag>
          <Tag>{cameras.length} 路相机</Tag>
          <Tag>{auxiliary.length} 项辅助数据</Tag>
        </Space>
      </div>
      {missingUnits && (
        <Alert
          showIcon
          type="warning"
          title="请确认关节单位"
          description="样本没有声明单位时不会自动推测。根据设备说明选择弧度 rad、米 m 等单位；同组关节单位一致时可批量填写。"
        />
      )}
      <h3 className={styles.groupTitle}>
        <Activity size={18} aria-hidden="true" />
        关节数据
      </h3>
      <div className={styles.grid}>{joints.map(field)}</div>
      <h3 className={styles.groupTitle}>
        <Camera size={18} aria-hidden="true" />
        相机画面 <span>{cameras.length} 路</span>
      </h3>
      {cameras.length ? (
        <div className={styles.grid}>{cameras.map(field)}</div>
      ) : (
        <p className={styles.hint}>
          当前方案不要求相机画面，可用于只采集关节数据。
        </p>
      )}
      {auxiliary.length > 0 && (
        <details className={styles.auxiliary}>
          <summary>
            <ListTree size={18} aria-hidden="true" />
            辅助数据 · {auxiliary.length} 项{" "}
            <span>位置姿态、时间和有效性记录</span>
          </summary>
          <p className={styles.hint}>
            从样本导入的字段会完整保留。通常无需逐项修改；取消“必须采集”后，该字段可以缺失。
          </p>
          <div className={styles.grid}>{auxiliary.map(field)}</div>
        </details>
      )}
      {!disabled && (
        <AddFeature
          existing={features}
          onAdd={(name, role) => set(name, newFeature(role))}
        />
      )}
    </div>
  );
}

function AddFeature({
  existing,
  onAdd,
}: {
  existing: Record<string, Feature>;
  onAdd: (name: string, role: Feature["role"]) => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState<Feature["role"]>("rgb");
  return (
    <details className={styles.details}>
      <summary>手动添加数据项</summary>
      <p className={styles.hint}>
        已有样本时优先从样本生成。手动添加时，字段名称必须与采集文件一致。
      </p>
      <Space wrap>
        <Select
          aria-label="新增字段角色"
          value={role}
          onChange={setRole}
          options={roles}
        />
        <Input
          aria-label="新增字段名称"
          placeholder="例如 observation.images.head"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button
          disabled={!name.trim() || Boolean(existing[name.trim()])}
          onClick={() => {
            onAdd(name.trim(), role);
            setName("");
          }}
        >
          添加字段
        </Button>
      </Space>
    </details>
  );
}
