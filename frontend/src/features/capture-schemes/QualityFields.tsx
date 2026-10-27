import { Checkbox, Collapse, Form, InputNumber, Select, Space } from "antd";
import type { SchemeDocument } from "./contracts";
import { qualityDefaults } from "./quality-contract";
import styles from "./definition.module.css";

const titles = {timing: "时间与连续性", completeness: "完整帧", synchronization: "多路同步",
  image: "图像", action: "动作", point_cloud: "点云"};
type Group = keyof typeof qualityDefaults;
type Values = Record<string, number | boolean | null>;
const labels: Record<string, string> = {
  minimum_frequency_ratio: "最小频率比例", minimum_coverage_ratio: "最小时间覆盖比例",
  maximum_gap_ns: "最大时间间隔（毫秒）", maximum_consecutive_missing: "最多连续缺帧数",
  maximum_duplicate_timestamps: "最多重复时间戳数", maximum_backward_timestamps: "最多倒退时间戳数",
  minimum_ratio: "最小完整帧比例", maximum_p95_offset_ns: "95% 分位同步误差（毫秒）",
  black_luma_threshold: "黑帧亮度界限", maximum_black_frame_ratio: "最大黑帧比例",
  maximum_repeated_frame_ratio: "最大重复帧比例", maximum_corrupt_frame_ratio: "最大损坏帧比例",
  minimum_observation_count: "最少动作记录数", maximum_jump: "最大相邻动作跳变量",
  dimension_mismatch_is_reject: "动作维数不匹配时拒绝", minimum_point_count: "最少点数",
  maximum_point_count: "最多点数", maximum_empty_ratio: "最大空点云比例",
  maximum_abnormal_count_ratio: "最大异常点数比例",
};
function Fields({group, value, onChange, prefix = ""}: {
  group: Group; value: object | undefined; onChange: (next: Values) => void; prefix?: string;
}) {
  const values = {...qualityDefaults[group], ...value} as Values;
  return <div className={styles.dimensions}>{Object.entries(qualityDefaults[group]).map(([key, fallback]) => {
    const kind = key.endsWith("_risk") ? "提示" : key.endsWith("_reject") ? "拒绝" : "";
    const label = `${prefix}${labels[key.replace(/_(risk|reject)$/, "")] ?? labels[key]}${kind ? ` · ${kind}` : ""}`;
    const scale = key.includes("_ns_") ? 1000000 : 1;
    const current = values[key];
    return <Form.Item key={key} label={label}>
      {typeof fallback === "boolean" ? <Checkbox aria-label={label} checked={Boolean(current)}
        onChange={e => onChange({...values, [key]: e.target.checked})}>启用</Checkbox>
        : <InputNumber aria-label={label} value={typeof current === "number" ? current / scale : null}
          min={0} max={key.includes("ratio") ? 1 : undefined} placeholder="不限制"
          step={key.includes("ratio") ? .01 : 1}
          onChange={next => onChange({...values, [key]: next === null ? null : next * scale})} />}
    </Form.Item>;
  })}</div>;
}

export function QualityFields({document, onChange}: {document: SchemeDocument; onChange: (next: SchemeDocument) => void}) {
  const policy = document.quality_policy;
  const update = (patch: object) => onChange({...document, quality_policy: {...policy, ...patch}});
  const features = Object.entries(document.output.features);
  const names = [...new Set(features.filter(([, f]) => f.role === "state")
    .flatMap(([, f]) => Array.isArray(f.names) ? f.names : []))];
  const limits = policy.joint_limits ?? {};
  return <section aria-label="采集方案质检规则" className={styles.card}>
    <h3>采集方案质检规则</h3>
    <p className={styles.hint}>字段定义和以下检查共同组成采集方案。修改后保存并发布，后续处理使用所选方案版本。比例范围为 0～1；频率按实际采集频率检查，不要求固定帧率。</p>
    <Collapse items={Object.entries(titles).map(([name, title]) => ({key: name, label: title,
      children: <Fields group={name as Group} value={policy[name as Group]}
        onChange={value => update({[name]: value})} />,
    })).concat([{key: "joints", label: "关节范围", children: <>
      <Select aria-label="检查范围的关节" mode="multiple" style={{width: "100%"}} value={Object.keys(limits)}
        options={names.map(value => ({value, label: value}))}
        onChange={selected => update({joint_limits: Object.fromEntries(selected.map(name => [name, limits[name] ?? [0, 0]]))})} />
      {Object.entries(limits).map(([name, bounds]) => <Form.Item key={name} label={name}><Space>
        <InputNumber aria-label={`${name} 下限`} value={bounds[0]} onChange={value => update({joint_limits: {...limits, [name]: [value ?? 0, bounds[1]]}})} />
        <InputNumber aria-label={`${name} 上限`} value={bounds[1]} onChange={value => update({joint_limits: {...limits, [name]: [bounds[0], value ?? 0]}})} />
      </Space></Form.Item>)}
      <Checkbox checked={policy.joint_out_of_range_is_reject ?? true} onChange={e => update({joint_out_of_range_is_reject: e.target.checked})}>关节超出范围时拒绝</Checkbox>
    </>}])} />
    <Collapse items={([['feature_timing', 'timing', '按字段调整时间检查'],
      ['feature_images', 'image', '按相机调整图像检查'],
      ['feature_point_clouds', 'point_cloud', '按字段调整点云检查']] as const).map(([key, group, title]) => {
        const overrides = policy[key] ?? {};
        const options = features.filter(([, f]) => group !== 'image' || ['rgb', 'depth'].includes(f.role))
          .map(([value]) => ({value, label: value}));
        return {key, label: title, children: <>
          <Select aria-label={title} mode="multiple" style={{width: '100%'}} options={options} value={Object.keys(overrides)}
            onChange={selected => update({[key]: Object.fromEntries(selected.map(name => [name, overrides[name] ?? policy[group] ?? qualityDefaults[group]]))})} />
          {Object.entries(overrides).map(([name, value]) => <div key={name}><h4>{name}</h4>
            <Fields group={group} value={value} prefix={`${name} `} onChange={next => update({[key]: {...overrides, [name]: next}})} />
          </div>)}
        </>};
      })} />
  </section>;
}
