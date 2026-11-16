import { Alert, Form, Input, Modal, Select } from "antd";
import { useState } from "react";
import {
  useAddQualityRule,
  type EngineRule,
} from "../../features/capture-schemes/api";
import { captureError } from "../../features/capture-schemes/presentation";
import {
  QualityParameters,
  type Values,
} from "../../features/capture-schemes/QualityParameters";
import { qualityFindingText } from "../../shared/lib/quality-presentation";

export function AddRuleDialog({
  org,
  rules,
  onClose,
  onSaved,
}: {
  org: string;
  rules: EngineRule[];
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const add = useAddQualityRule(org);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<EngineRule>();
  const [values, setValues] = useState<Values>({});
  const [error, setError] = useState("");
  function save() {
    if (!name.trim() || !selected) {
      setError("请填写规则名称并选择检查项。");
      return;
    }
    if (
      Object.entries(selected.defaults).some(
        ([key, fallback]) => fallback !== null && values[key] == null,
      )
    ) {
      setError("请补全必填阈值。");
      return;
    }
    setError("");
    add.mutate(
      { name: name.trim(), code: selected.code, parameters: values },
      {
        onSuccess: (rule) => onSaved(rule.name),
      },
    );
  }
  return (
    <Modal
      open
      title="新增质检规则"
      okText="保存规则"
      cancelText="取消"
      onOk={save}
      onCancel={() => {
        if (!add.isPending) onClose();
      }}
      confirmLoading={add.isPending}
    >
      <Form layout="vertical" disabled={add.isPending}>
        <Form.Item label="规则名称" required>
          <Input
            aria-label="规则名称"
            maxLength={100}
            placeholder="例如：高频采集最低频率"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Form.Item>
        <Form.Item label="检查项" required>
          <Select
            aria-label="检查项"
            placeholder="选择要配置的检查项"
            value={selected?.code}
            options={rules
              .filter((rule) => rule.group)
              .map((rule) => ({
                value: rule.code,
                label: qualityFindingText(rule.code).title,
              }))}
            onChange={(code) => {
              const rule = rules.find((item) => item.code === code);
              setSelected(rule);
              setValues(rule?.defaults ?? {});
              setError("");
              add.reset();
            }}
          />
        </Form.Item>
        {selected && (
          <QualityParameters
            defaults={selected.defaults}
            values={values}
            onChange={setValues}
          />
        )}
        {(error || add.error) && (
          <Alert
            type="error"
            showIcon
            title={error || captureError(add.error)}
          />
        )}
      </Form>
    </Modal>
  );
}
