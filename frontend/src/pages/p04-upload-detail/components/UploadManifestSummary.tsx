import { Alert, Descriptions, Typography } from "antd";
import type { SourceManifestSummary } from "../../../features/ingest/upload/model";
import { StatusTag } from "../../../shared/ui";

const manifestStatusLabels: Readonly<Record<string, string>> = {
  RECEIVED: "已接收",
  PARSED: "已解析",
  INVALID: "无效",
};

function statusLabel(status: string): string {
  return manifestStatusLabels[status] ?? status;
}

export function UploadManifestSummary({
  manifest,
}: {
  readonly manifest: SourceManifestSummary | null;
}) {
  if (!manifest) {
    return (
      <Alert
        type="info"
        showIcon
        title="尚未提交清单"
        description="上传完成并提交清单后，此处将显示清单摘要。"
      />
    );
  }

  return (
    <Descriptions
      bordered
      size="small"
      column={{ xs: 1, sm: 1, md: 2 }}
      items={[
        {
          key: "manifestId",
          label: "清单 ID",
          children: (
            <Typography.Text code>{manifest.manifestId}</Typography.Text>
          ),
        },
        { key: "revision", label: "修订号", children: manifest.revision },
        {
          key: "status",
          label: "状态",
          children: (
            <StatusTag
              status={manifest.status}
              label={statusLabel(manifest.status)}
              tone={
                manifest.status === "PARSED"
                  ? "success"
                  : manifest.status === "INVALID"
                    ? "danger"
                    : "info"
              }
            />
          ),
        },
        {
          key: "schemaVersion",
          label: "结构版本",
          children: manifest.schemaVersion,
        },
        {
          key: "canonicalization",
          label: "规范化方式",
          children: manifest.canonicalization,
        },
        {
          key: "sourceFormat",
          label: "源格式",
          children: manifest.sourceFormat,
        },
        {
          key: "sourceFormatVersion",
          label: "源格式版本",
          children: manifest.sourceFormatVersion ?? "—",
        },
        {
          key: "adapterVersion",
          label: "适配器版本",
          children: manifest.adapterVersion,
        },
        {
          key: "declaredObjectCount",
          label: "声明对象数",
          children: manifest.declaredObjectCount,
        },
        {
          key: "declaredBytes",
          label: "声明总大小",
          children: `${manifest.declaredBytes} B`,
        },
        {
          key: "submittedBy",
          label: "提交人",
          children: manifest.submittedBy.displayName,
        },
        {
          key: "submittedAt",
          label: "提交时间",
          children: manifest.submittedAt,
        },
        {
          key: "issues",
          label: "结构问题",
          children: `错误 ${manifest.schemaIssueCounts.error} · 警告 ${manifest.schemaIssueCounts.warning} · 信息 ${manifest.schemaIssueCounts.info}`,
        },
        {
          key: "sha256",
          label: "清单 SHA-256",
          span: 2,
          children: <Typography.Text code>{manifest.sha256}</Typography.Text>,
        },
        {
          key: "objectSetHash",
          label: "对象集哈希",
          span: 2,
          children: (
            <Typography.Text code>{manifest.objectSetHash}</Typography.Text>
          ),
        },
      ]}
    />
  );
}
