import { Checkbox, Form, Input, InputNumber, Select, Tooltip } from "antd";
import { CircleHelp } from "lucide-react";
import type { McapConfig, SchemeDocument, SourceFormat } from "./contracts";
import { mcapConfiguration } from "./editor-model";
import styles from "./definition.module.css";

function SourceHelp({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip
      title={text}
      trigger={["hover", "focus"]}
      styles={{ container: { fontSize: 12, lineHeight: 1.6 } }}
    >
      <button
        type="button"
        className={styles.sourceHelp}
        aria-label={label}
        onClick={(event) => event.currentTarget.focus()}
      >
        <CircleHelp size={14} aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

export function AdapterFields({
  document: doc,
  onChange,
}: {
  document: SchemeDocument;
  onChange: (doc: SchemeDocument) => void;
}) {
  const formats = doc.adapters.map((a) => a.source_format);
  const changeMcap = (
    patch: Partial<McapConfig>,
    format: SourceFormat = "MCAP",
  ) =>
    onChange({
      ...doc,
      adapters: doc.adapters.map((a) =>
        a.source_format !== "LEROBOT_V3" && a.source_format === format
          ? { ...a, configuration: { ...a.configuration, ...patch } }
          : a,
      ),
    });
  return (
    <>
      <Form.Item
        label={
          <span className={styles.sourceLabel}>
            原始文件格式
            <SourceHelp
              label="原始文件格式说明"
              text="选择设备上传的原始文件，可多选并分别验证。导出格式在导出任务中另选；ROS Bag 暂不支持此方案。"
            />
          </span>
        }
      >
        <Checkbox.Group
          value={formats}
          options={[
            { value: "LEROBOT_V3", label: "LeRobot v3（数据集目录）" },
            { value: "MCAP", label: "MCAP（图像在话题文件内）" },
            { value: "CAPTURE_BUNDLE", label: "MCAP＋独立 MP4（原始包）" },
          ]}
          onChange={(values) =>
            onChange({
              ...doc,
              adapters: (values as SourceFormat[]).map(
                (format) =>
                  doc.adapters.find((a) => a.source_format === format) ??
                  (format !== "LEROBOT_V3"
                    ? {
                        ...(format === "MCAP"
                          ? {
                              source_format: "MCAP" as const,
                              parser_version:
                                "native-mcap-recording/2" as const,
                            }
                          : {
                              source_format: "CAPTURE_BUNDLE" as const,
                              parser_version: "capture-bundle/1" as const,
                            }),
                        configuration: mcapConfiguration(doc, format),
                      }
                    : {
                        source_format: "LEROBOT_V3",
                        parser_version: "lerobot-v3/1",
                        codebase_version: "v3.0",
                        feature_sources: {},
                      }),
              ),
            })
          }
        />
      </Form.Item>
      {doc.adapters.map((adapter) =>
        adapter.source_format === "LEROBOT_V3" ? (
          <section key="lerobot" className={styles.sourceSection}>
            <h3 className={styles.sourceHeading}>
              LeRobot 数据读取
              <SourceHelp
                label="LeRobot 字段说明"
                text="字段同名时自动读取；原文件名称不同时，再设置对应关系。"
              />
              {Object.keys(adapter.feature_sources).length > 0 && (
                <span className={styles.sourceStatus}>
                  {Object.keys(adapter.feature_sources).length} 项自定义
                </span>
              )}
            </h3>
            <details
              className={styles.details}
              open={
                Object.keys(adapter.feature_sources).length > 0 || undefined
              }
            >
              <summary>字段名称不同？设置对应关系</summary>
              <p className={styles.hint}>
                例如：方案中的机器人状态叫 observation.state，原文件中叫
                robot.joints，就在机器人状态的输入框填写 robot.joints。填写
                meta/info.json 中 features 下的完整字段名；留空使用同名字段。
              </p>
              {Object.entries(doc.output.features).map(([name, feature]) => (
                <Form.Item
                  key={name}
                  label={
                    <span>
                      {feature.role === "state"
                        ? "机器人状态"
                        : feature.role === "action"
                          ? "控制动作"
                          : feature.role === "rgb"
                            ? "彩色相机"
                            : feature.role === "depth"
                              ? "深度相机"
                              : "辅助数据"}{" "}
                      · <code>{name}</code>
                    </span>
                  }
                  extra={`默认读取原文件中的 ${name}`}
                >
                  <Input
                    aria-label={`${name} LeRobot 来源`}
                    value={adapter.feature_sources[name] ?? ""}
                    placeholder="自动匹配同名字段"
                    onChange={(e) => {
                      const sources = { ...adapter.feature_sources };
                      if (e.target.value) sources[name] = e.target.value;
                      else delete sources[name];
                      onChange({
                        ...doc,
                        adapters: doc.adapters.map((a) =>
                          a.source_format === "LEROBOT_V3"
                            ? { ...a, feature_sources: sources }
                            : a,
                        ),
                      });
                    }}
                  />
                </Form.Item>
              ))}
            </details>
          </section>
        ) : (
          <section key={adapter.source_format} className={styles.sourceSection}>
            <h3 className={styles.sourceHeading}>
              {adapter.source_format === "CAPTURE_BUNDLE"
                ? "MCAP＋MP4 原始包读取"
                : "MCAP 消息适配"}
              <SourceHelp
                label="MCAP 读取说明"
                text="按原始帧率读取，支持 1–240 Hz 整数。状态与动作可分别配置；话题设置用于读取设备消息。"
              />
            </h3>
            <Form.Item label="状态话题">
              <Input
                aria-label={`${adapter.source_format} 状态话题`}
                value={adapter.configuration.state_topic ?? ""}
                onChange={(e) =>
                  changeMcap(
                    { state_topic: e.target.value },
                    adapter.source_format,
                  )
                }
              />
            </Form.Item>
            <Form.Item label="动作话题">
              <Input
                aria-label={`${adapter.source_format} 动作话题`}
                value={adapter.configuration.action_topic ?? ""}
                onChange={(e) =>
                  changeMcap(
                    { action_topic: e.target.value },
                    adapter.source_format,
                  )
                }
              />
            </Form.Item>
            <Form.Item label="时间来源">
              <Select
                aria-label={`${adapter.source_format} 时间来源`}
                value={adapter.configuration.time_source}
                options={[
                  { value: "STATE_LOG_TIME", label: "状态消息记录时间" },
                  { value: "RGB_LOG_TIME", label: "主相机消息记录时间" },
                ]}
                onChange={(time_source) =>
                  onChange({
                    ...doc,
                    output: { ...doc.output, time_source },
                    adapters: doc.adapters.map((a) =>
                      a.source_format !== "LEROBOT_V3"
                        ? {
                            ...a,
                            configuration: { ...a.configuration, time_source },
                          }
                        : a,
                    ),
                  })
                }
              />
            </Form.Item>
            {Object.entries(doc.output.features)
              .filter(([, f]) => f.role === "rgb" || f.role === "depth")
              .map(([name, f]) => {
                const id = name.replace(/^observation.images\./, "");
                const camera = adapter.configuration.cameras.find(
                  (c) => c.camera_id === id,
                );
                const patch = (value: {
                  topic?: string;
                  message_type?: string;
                }) => {
                  const next = {
                    camera_id: id,
                    topic: "",
                    message_type: "",
                    ...camera,
                    kind: f.role as "rgb" | "depth",
                    required: f.required,
                    ...value,
                  };
                  changeMcap(
                    {
                      collection_mode: "RGB",
                      cameras: [
                        ...adapter.configuration.cameras.filter(
                          (c) => c.camera_id !== id,
                        ),
                        next,
                      ],
                    },
                    adapter.source_format,
                  );
                };
                return (
                  <section key={name}>
                    <h4>
                      {name} · {f.required ? "必需" : "可选"}
                    </h4>
                    <Form.Item label="相机话题">
                      <Input
                        aria-label={`${name} 相机话题`}
                        value={camera?.topic ?? ""}
                        onChange={(e) => patch({ topic: e.target.value })}
                      />
                    </Form.Item>
                    <Form.Item label="消息类型">
                      <Input
                        aria-label={`${name} 消息类型`}
                        placeholder="填写真实消息类型"
                        value={camera?.message_type ?? ""}
                        onChange={(e) =>
                          patch({ message_type: e.target.value })
                        }
                      />
                    </Form.Item>
                  </section>
                );
              })}
            <Form.Item label="主相机">
              <Select
                aria-label="主相机"
                allowClear
                value={adapter.configuration.primary_camera ?? undefined}
                options={adapter.configuration.cameras
                  .filter((c) => c.kind === "rgb")
                  .map((c) => ({ value: c.topic, label: c.camera_id }))}
                onChange={(v) =>
                  changeMcap(
                    { primary_camera: v ?? null },
                    adapter.source_format,
                  )
                }
              />
            </Form.Item>
            {adapter.configuration.cameras.some((c) => c.kind === "depth") && (
              <>
                <Form.Item label="深度单位换算（米/原始单位）">
                  <InputNumber
                    aria-label="深度单位换算"
                    min={0.000001}
                    max={1}
                    value={adapter.configuration.depth_scale_m_per_unit}
                    onChange={(v) =>
                      changeMcap(
                        { depth_scale_m_per_unit: v },
                        adapter.source_format,
                      )
                    }
                  />
                </Form.Item>
                <Form.Item label="深度单位证据">
                  <Input
                    value={adapter.configuration.depth_unit_evidence ?? ""}
                    onChange={(e) =>
                      changeMcap(
                        { depth_unit_evidence: e.target.value },
                        adapter.source_format,
                      )
                    }
                  />
                </Form.Item>
              </>
            )}
          </section>
        ),
      )}
    </>
  );
}
