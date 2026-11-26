import { useState } from "react";
import { Button, Flex, Input, Select, Space, Tag, Typography } from "antd";
import { capabilityLabel, permissionLabel } from "./presentation";

const resourceHints: Record<string, string> = {
  "teleoperation.connect": "机器人 ID",
  "collection_task.delete": "采集任务 ID",
  "dataset.delete": "数据集 ID",
  "dataset_version.delete": "数据集ID/版本ID",
};
const downloads = new Set(["export.download", "dataset_version.download_raw", "dataset_version.download_manifest"]);

export function MemberPermissionEditor({ assigned, assignable, save }: {
  assigned: string[];
  assignable: string[];
  save: (selected: string[], expected: string[], done: () => void) => void;
}) {
  const [draft, setDraft] = useState<{ selected: string[]; expected: string[] }>();
  const [choice, setChoice] = useState<string>();
  const [target, setTarget] = useState("");
  const current = draft?.selected ?? assigned;
  const hint = resourceHints[choice ?? ""];
  const download = downloads.has(choice ?? "");
  const resource = target.trim();
  const validTarget = hint
    ? (choice === "dataset_version.delete" ? /^[\w.:-]+\/[\w.:-]+$/ : /^[\w.:-]+$/).test(resource)
    : !download || !resource || /^(dataset|version):[\w.:-]+$/.test(resource);
  const next = choice && resource ? choice + "/" + resource : choice;
  const changed = !!draft && (draft.expected.length !== current.length || current.some(k => !draft.expected.includes(k)));
  function change(selected: string[]) {
    setDraft({ selected, expected: draft?.expected ?? assigned });
  }
  return <section aria-label="直接分配的专项权限">
    <Typography.Title level={5}>直接分配的专项权限</Typography.Title>
    <Space direction="vertical" style={{ display: "flex" }}>
      <Flex wrap gap={4}>{current.length ? current.map(key =>
        <Tag key={key} closable={assignable.includes(key.split("/")[0] ?? "")}
          onClose={() => change(current.filter(item => item !== key))}>{permissionLabel(key)}</Tag>
      ) : <Typography.Text type="secondary">尚未直接分配专项权限</Typography.Text>}</Flex>
      {!!assignable.length && <>
        <Select aria-label="选择专项权限" placeholder="搜索并选择权限" value={choice} allowClear
          style={{ width: "100%" }} showSearch={{ filterOption: (input, option) =>
            (String(option?.label) + " " + String(option?.value)).toLowerCase().includes(input.toLowerCase()) }}
          options={assignable.map(value => ({ value, label: capabilityLabel(value) }))}
          onChange={value => { setChoice(value); setTarget(""); }} />
        {choice === "annotation.save" && <Typography.Text type="secondary">
          运行模型预标注还需查看标注任务权限，保存结果时会核对任务负责人。
        </Typography.Text>}
        {choice === "teleoperation.connect" && <Typography.Text type="secondary">
          在本组织内连接指定机器人。查看机器人列表还需“查看机器人”权限。
        </Typography.Text>}
        {(hint || download) && <label>{hint ?? "下载范围（留空表示整个项目）"}
          <Input aria-label={hint ?? "下载范围"} maxLength={256} value={target}
            placeholder={hint ?? "dataset:数据集ID 或 version:版本ID"} onChange={e => setTarget(e.target.value)} />
        </label>}
        <Flex gap="small" wrap>
          <Button disabled={!next || !validTarget || current.includes(next)} onClick={() => {
            if (next) change([...current, next]);
            setChoice(undefined); setTarget("");
          }}>加入列表</Button>
          <Button type="primary" disabled={!changed} onClick={() => {
            if (draft) save(draft.selected, draft.expected, () => setDraft(undefined));
          }}>保存专项权限</Button>
          {draft && <Button type="text" onClick={() => setDraft(undefined)}>取消修改</Button>}
        </Flex>
      </>}
    </Space>
  </section>;
}
