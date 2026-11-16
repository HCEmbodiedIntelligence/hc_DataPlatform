import { Alert, Button, Select, Space } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQualityRules } from "./api";
import type { SchemeDocument } from "./contracts";
import { captureError } from "./presentation";
import { qualityPolicySchema } from "./quality-contract";
import { qualityFindingText } from "../../shared/lib/quality-presentation";

export function ApplyQualityRule({
  org,
  document,
  onChange,
}: {
  org: string;
  document: SchemeDocument;
  onChange: (next: SchemeDocument) => void;
}) {
  const [opened, setOpened] = useState(false);
  const rules = useQualityRules(org, opened);
  const [selected, setSelected] = useState<string>();
  const [applied, setApplied] = useState("");
  const rule = rules.data?.custom_rules.find((item) => item.name === selected);
  const group = rules.data?.engine_rules.find(
    (item) => item.code === rule?.code,
  )?.group;
  function apply() {
    if (!rule || !group) return;
    const policy = document.quality_policy;
    const settings = qualityPolicySchema.shape[group].parse({
      ...policy[group],
      ...rule.parameters,
    });
    onChange({ ...document, quality_policy: { ...policy, [group]: settings } });
    setApplied(rule.name);
    setSelected(undefined);
  }
  return (
    <section aria-label="从规则库应用质检规则">
      <Space wrap>
        <Select
          aria-label="选择质检规则"
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 270 }}
          placeholder="选择组织的自定义规则"
          value={selected}
          loading={rules.isFetching}
          onOpenChange={(open) => {
            if (open) setOpened(true);
          }}
          notFoundContent={rules.isFetching ? "正在读取规则" : "暂无自定义规则"}
          options={(rules.data?.custom_rules ?? []).map((item) => ({
            value: item.name,
            label: item.name + " · " + qualityFindingText(item.code).title,
          }))}
          onChange={(name) => {
            setSelected(name);
            setApplied("");
          }}
        />
        <Button disabled={!rule || !group} onClick={apply}>
          应用到草稿
        </Button>
        <Link to="/settings/quality-rules" target="_blank" rel="noreferrer">
          打开质检规则库
        </Link>
      </Space>
      {applied && <p role="status">已将「{applied}」的阈值填入当前草稿。</p>}
      {rules.error && (
        <Alert
          type="error"
          title={captureError(rules.error)}
          action={
            <Button size="small" onClick={() => void rules.refetch()}>
              重试
            </Button>
          }
        />
      )}
    </section>
  );
}
