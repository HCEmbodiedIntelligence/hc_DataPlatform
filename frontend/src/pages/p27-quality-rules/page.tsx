import { Alert, Button, Input, Select, Space, Table, Tag } from "antd";
import { useState } from "react";
import { useQualityRules } from "../../features/capture-schemes/api";
import { captureError } from "../../features/capture-schemes/presentation";
import {
  parameterLabel,
  parameterScale,
  type Values,
} from "../../features/capture-schemes/QualityParameters";
import { qualityFindingText } from "../../shared/lib/quality-presentation";
import { useCurrentOrganizationId } from "../../shared/scope/current-organization";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { StandardPageScaffold } from "../../shared/ui";
import { inspectionDescriptions } from "./inspection-descriptions";
import { AddRuleDialog } from "./AddRuleDialog";

const categories = [
  "自定义规则",
  "质检引擎",
  "录制检查",
  "MCAP 原始文件",
] as const;
type Row = {
  name: string;
  code: string;
  category: (typeof categories)[number];
  description?: string;
  parameters?: Values;
};

function duplicateLimits(values: Values) {
  const risk = values.maximum_duplicate_timestamps_risk;
  const reject = values.maximum_duplicate_timestamps_reject;
  return `同一通道重复超过 ${risk} 次时提示。` +
    (typeof reject === "number" ? `超过 ${reject} 次时拒绝，优先按拒绝判定。` : "未设拒绝次数，仅凭重复次数不会拒绝。");
}

function Thresholds({ values }: { values: Values }) {
  return (
    <div>
      {Object.entries(values).map(([key, raw]) => {
        const value =
          typeof raw === "number"
            ? key.includes("ratio") ? `${Number((raw * 100).toFixed(6))}%` : raw / parameterScale(key)
            : raw === null
              ? "不限制"
              : raw
                ? "启用"
                : "关闭";
        return (
          <div key={key}>
            {parameterLabel(key)}：{value}
          </div>
        );
      })}
    </div>
  );
}

export function Component() {
  const org = useCurrentOrganizationId();
  return org ? (
    <Library key={org} org={org} />
  ) : (
    <Alert type="info" title="请先选择组织" />
  );
}

function Library({ org }: { org: string }) {
  const rights = useOrganizationCapabilities(org);
  const list = useQualityRules(org, rights.has("robot.read"));
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Row["category"]>();
  const [saved, setSaved] = useState("");
  const data = list.data;
  const rows: Row[] = data
    ? [
        ...data.custom_rules.map((rule) => ({
          name: rule.name,
          code: rule.code,
          category: categories[0],
          description: qualityFindingText(rule.code).description,
          parameters: rule.parameters,
        })),
        ...data.engine_rules.map((rule) => ({
          name: qualityFindingText(rule.code).title,
          code: rule.code,
          description: qualityFindingText(rule.code).description,
          category: categories[1],
          parameters: rule.defaults,
        })),
        ...Object.entries(data.recording_checks).map(([code, name]) => ({
          name,
          code,
          category: categories[2],
          description: inspectionDescriptions[code],
        })),
        ...Object.entries(data.verification_checks).map(([code, name]) => ({
          name,
          code,
          category: categories[3],
          description: inspectionDescriptions[code],
        })),
      ]
    : [];
  const term = query.trim().toLowerCase();
  const filtered = rows.filter(
    (row) =>
      (!category || row.category === category) &&
      [row.name, row.code, row.description ?? ""].some((v) =>
        v.toLowerCase().includes(term),
      ),
  );
  return (
    <StandardPageScaffold
      header={{
        title: "质检规则",
        breadcrumbs: [
          { key: "governance", label: "治理与资产" },
          { key: "rules", label: "质检规则" },
        ],
        description:
          "查看各阶段的检查项。自定义规则保存到当前组织，应用到采集方案并发布后生效。",
        actions: rights.has("quality.manage") && (
          <Button
            type="primary"
            disabled={!data}
            onClick={() => setAdding(true)}
          >
            新增质检规则
          </Button>
        ),
      }}
      filters={
        <Space wrap>
          <Input
            aria-label="搜索质检规则"
            placeholder="搜索规则名称或代码"
            allowClear
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Select
            aria-label="规则来源"
            placeholder="全部来源"
            allowClear
            value={category}
            style={{ minWidth: 170 }}
            options={categories.map((value) => ({ value, label: value }))}
            onChange={setCategory}
          />
        </Space>
      }
    >
      {saved && (
        <Alert type="success" showIcon title={`已保存规则「${saved}」`} />
      )}
      {list.error && (
        <Alert
          type="error"
          showIcon
          title={captureError(list.error)}
          action={
            <Button size="small" onClick={() => void list.refetch()}>
              重试
            </Button>
          }
        />
      )}
      <Table<Row>
        rowKey={(row) => JSON.stringify([row.category, row.code, row.name])}
        loading={list.isFetching}
        dataSource={filtered}
        scroll={{ x: 780 }}
        pagination={{
          defaultPageSize: 15,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        locale={{ emptyText: list.error ? "规则读取失败" : "没有匹配的规则" }}
        columns={[
          { title: "规则名称", dataIndex: "name" },
          {
            title: "来源",
            dataIndex: "category",
            render: (value) => <Tag>{value}</Tag>,
          },
          { title: "检查代码", dataIndex: "code" },
          {
            title: "规则参数",
            render: (_, row) =>
              row.parameters && Object.keys(row.parameters).length ? (
                <details>
                  <summary>
                    {row.category === categories[0]
                      ? "查看阈值"
                      : "查看默认阈值"}
                  </summary>
                  <Thresholds values={row.parameters} />
                </details>
              ) : row.code === "QC_JOINT_OUT_OF_RANGE" ? (
                "按方案关节配置"
              ) : (
                "系统检查"
              ),
          },
        ]}
        expandable={{
          rowExpandable: (row) => !!row.description,
          expandedRowRender: (row) => (
            <div>
              <p>{row.description}</p>
              {row.parameters && Object.keys(row.parameters).length > 0 && (
                <div>
                  <strong>{row.category === categories[0] ? "此模板的阈值" : "默认阈值"}</strong>
                  {row.code === "QC_TIMESTAMP_DUPLICATE" ? (
                    <p>{duplicateLimits(row.parameters)}</p>
                  ) : <Thresholds values={row.parameters} />}
                </div>
              )}
            </div>
          ),
        }}
      />
      {adding && data && (
        <AddRuleDialog
          org={org}
          rules={data.engine_rules}
          onClose={() => setAdding(false)}
          onSaved={(name) => {
            setSaved(name);
            setAdding(false);
            setCategory(categories[0]);
            setQuery(name);
          }}
        />
      )}
    </StandardPageScaffold>
  );
}
