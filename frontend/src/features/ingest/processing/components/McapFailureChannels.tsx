import { Table } from "antd";

type Channel = { topic: string; schema?: string; message_count?: number };

export function McapFailureChannels({
  inventory, error, configuration,
}: {
  inventory: unknown;
  error: string | null;
  configuration: Record<string, unknown>;
}) {
  const rows: Channel[] = [];
  if (inventory && typeof inventory === "object" && !Array.isArray(inventory)) {
    for (const [topic, value] of Object.entries(inventory)) {
      if (!value || typeof value !== "object") continue;
      rows.push({
        topic,
        schema: typeof value.schema === "string" ? value.schema : undefined,
        message_count: typeof value.message_count === "number" ? value.message_count : undefined,
      });
    }
  }
  if (!rows.length) return <p>本次处理没有保存原始话题清单。</p>;
  const priority = (topic: string) => error?.includes(topic) ? 0
    : [configuration.action_topic, configuration.state_topic, configuration.primary_camera].includes(topic) ? 1 : 2;
  rows.sort((a, b) => priority(a.topic) - priority(b.topic) || a.topic.localeCompare(b.topic));
  return (
    <section aria-label="原始话题数据">
      <strong>文件中读到的话题（{rows.length} 个）</strong>
      <Table<Channel>
        size="small"
        rowKey="topic"
        dataSource={rows}
        pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false, size: "small" }}
        scroll={{ x: 560 }}
        columns={[
          { title: "话题", dataIndex: "topic" },
          { title: "实际消息类型", dataIndex: "schema", render: value => value || "未记录" },
          { title: "消息数", dataIndex: "message_count", render: value => value ?? "未记录" },
        ]}
      />
    </section>
  );
}
