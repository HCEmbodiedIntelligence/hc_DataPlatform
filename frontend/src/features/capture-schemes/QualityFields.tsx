import { QualityParameters, type Values } from "./QualityParameters";
import { Checkbox, Collapse, Form, InputNumber, Select, Space } from "antd";
import type { SchemeDocument } from "./contracts";
import { qualityDefaults } from "./quality-contract";
import type { EngineRule } from "./api";

type Group = keyof typeof qualityDefaults;
function Fields({
  group,
  value,
  onChange,
  prefix = "",
}: {
  group: Group;
  value: object | undefined;
  onChange: (next: Values) => void;
  prefix?: string;
}) {
  const values = { ...qualityDefaults[group], ...value } as Values;
  return (
    <QualityParameters
      defaults={qualityDefaults[group]}
      values={values}
      prefix={prefix}
      onChange={onChange}
    />
  );
}

export function QualityFields({
  document,
  onChange,
  rule,
}: {
  document: SchemeDocument;
  onChange: (next: SchemeDocument) => void;
  rule: EngineRule;
}) {
  const policy = document.quality_policy;
  const update = (patch: object) =>
    onChange({ ...document, quality_policy: { ...policy, ...patch } });
  const features = Object.entries(document.output.features);
  const names = [
    ...new Set(
      features
        .filter(([, f]) => f.role === "state")
        .flatMap(([, f]) => (Array.isArray(f.names) ? f.names : [])),
    ),
  ];
  const limits = policy.joint_limits ?? {};
  const group = rule.group;
  const jointRule = rule.code === "QC_JOINT_OUT_OF_RANGE";
  return (
    <>
      {group && (
        <QualityParameters
          defaults={rule.defaults}
          values={{ ...rule.defaults, ...policy[group] } as Values}
          onChange={(value) =>
            update({ [group]: { ...policy[group], ...value } })
          }
        />
      )}
      {jointRule && (
        <>
          <Select
            aria-label="检查范围的关节"
            mode="multiple"
            style={{ width: "100%" }}
            value={Object.keys(limits)}
            options={names.map((value) => ({ value, label: value }))}
            onChange={(selected) =>
              update({
                joint_limits: Object.fromEntries(
                  selected.map((name) => [name, limits[name] ?? [0, 0]]),
                ),
              })
            }
          />
          {Object.entries(limits).map(([name, bounds]) => (
            <Form.Item key={name} label={name}>
              <Space>
                <InputNumber
                  aria-label={`${name} 下限`}
                  value={bounds[0]}
                  onChange={(value) =>
                    update({
                      joint_limits: {
                        ...limits,
                        [name]: [value ?? 0, bounds[1]],
                      },
                    })
                  }
                />
                <InputNumber
                  aria-label={`${name} 上限`}
                  value={bounds[1]}
                  onChange={(value) =>
                    update({
                      joint_limits: {
                        ...limits,
                        [name]: [bounds[0], value ?? 0],
                      },
                    })
                  }
                />
              </Space>
            </Form.Item>
          ))}
          <Checkbox
            checked={policy.joint_out_of_range_is_reject ?? true}
            onChange={(e) =>
              update({ joint_out_of_range_is_reject: e.target.checked })
            }
          >
            关节超出范围时拒绝
          </Checkbox>
        </>
      )}
      {(["timing", "image", "point_cloud"] as const).some(
        (key) => key === group,
      ) && (
        <Collapse
          items={(
            [
              ["feature_timing", "timing", "按字段调整时间检查"],
              ["feature_images", "image", "按相机调整图像检查"],
              ["feature_point_clouds", "point_cloud", "按字段调整点云检查"],
            ] as const
          )
            .filter(([, keyGroup]) => keyGroup === group)
            .map(([key, group, title]) => {
              const overrides = policy[key] ?? {};
              const options = features
                .filter(
                  ([, f]) =>
                    group !== "image" || ["rgb", "depth"].includes(f.role),
                )
                .map(([value]) => ({ value, label: value }));
              return {
                key,
                label: title,
                children: (
                  <>
                    <Select
                      aria-label={title}
                      mode="multiple"
                      style={{ width: "100%" }}
                      options={options}
                      value={Object.keys(overrides)}
                      onChange={(selected) =>
                        update({
                          [key]: Object.fromEntries(
                            selected.map((name) => [
                              name,
                              overrides[name] ??
                                policy[group] ??
                                qualityDefaults[group],
                            ]),
                          ),
                        })
                      }
                    />
                    {Object.entries(overrides).map(([name, value]) => (
                      <div key={name}>
                        <h4>{name}</h4>
                        <Fields
                          group={group}
                          value={value}
                          prefix={`${name} `}
                          onChange={(next) =>
                            update({ [key]: { ...overrides, [name]: next } })
                          }
                        />
                      </div>
                    ))}
                  </>
                ),
              };
            })}
        />
      )}
    </>
  );
}
