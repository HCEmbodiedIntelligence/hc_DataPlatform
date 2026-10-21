import { Alert, Descriptions, Table, Tag } from "antd";
import type { Difference, Evidence } from "./contracts";
import { captureProblemLabels } from "./presentation";
const readable = (value: unknown) =>
  value === undefined || value === null
    ? "未定义"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);
export function Differences({ items }: { items: Difference[] }) {
  return items.length ? (
    <Table
      size="small"
      pagination={false}
      rowKey="path"
      dataSource={items}
      columns={[
        { title: "字段", dataIndex: "path" },
        { title: "原定义 / 期望", dataIndex: "expected", render: readable },
        { title: "新定义 / 实际", dataIndex: "actual", render: readable },
      ]}
    />
  ) : (
    <p>没有结构差异。</p>
  );
}
export function EvidenceView({ items }: { items: Evidence[] }) {
  return (
    <>
      {items.map((e, index) => (
        <section key={`${e.source_format}:${index}`}>
          <Tag color={e.status === "PASSED" ? "green" : "red"}>
            {e.source_format} ·{" "}
            {e.status === "PASSED" ? "验证通过" : "验证未通过"}{" "}
            {e.revision ? `· 方案修订 ${e.revision}` : ""}
          </Tag>
          {e.error_code && (
            <Alert
              type="error"
              title={captureProblemLabels[e.error_code] ?? e.error_code}
              description={e.error_detail}
            />
          )}
          <Differences items={e.differences} />
          <Descriptions
            size="small"
            column={1}
            items={Object.entries(e.presence).map(([name, present]) => ({
              key: name,
              label: name,
              children: present ? "已采集" : "未采集",
            }))}
          />
          <details>
            <summary>验证证据</summary>
            <p>
              解析器：{e.parser_version} · 验证时间：{e.validated_at}
            </p>
            <p>样本摘要：{e.sample_sha256}</p>
            <p>方案摘要：{e.draft_sha256}</p>
          </details>
        </section>
      ))}
    </>
  );
}
