import { Avatar, Descriptions, Empty, Typography } from "antd";
import { History, ShieldCheck, UserRound } from "lucide-react";
import { StatusTag } from "../../../shared/ui";
import type { AccessRequestRow } from "../contracts";
import {
  accessRequestSummary,
  capabilityLabel,
  formatDateTime,
  shortIdentity,
  statusLabel,
  statusTone,
} from "../presentation";
import styles from "../styles.module.css";

export function AccessRequestDetails({
  row,
}: {
  readonly row: AccessRequestRow | null;
}) {
  if (!row) {
    return <Empty description="从左侧选择一条申请查看详情" />;
  }
  return (
    <div className={styles.detailContent}>
      <section className={styles.applicantIdentity} aria-label="申请人身份">
        <Avatar size={48} icon={<UserRound aria-hidden="true" />} />
        <div>
          <Typography.Title level={3} title={row.requesterId}>
            {shortIdentity(row.requesterId)}
          </Typography.Title>
          <span>申请人安全标识</span>
        </div>
        <StatusTag
          status={row.status}
          label={statusLabel(row.status)}
          tone={statusTone(row.status)}
        />
      </section>

      <section className={styles.detailBlock}>
        <header>
          <ShieldCheck aria-hidden="true" size={17} />
          <h3>申请概述</h3>
        </header>
        <Descriptions column={1} size="small" colon={false}>
          <Descriptions.Item label="申请类型">
            {row.kind === "membership" ? "加入项目申请" : "权限申请"}
          </Descriptions.Item>
          <Descriptions.Item label="目标项目">
            <Typography.Text code title={row.projectId}>
              {row.projectId}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item
            label={row.kind === "membership" ? "申请原因" : "权限摘要"}
          >
            {accessRequestSummary(row)}
          </Descriptions.Item>
          {row.kind === "capability" ? (
            <Descriptions.Item label="Capability">
              <ul className={styles.capabilityList}>
                {row.capabilityKeys.map((key) => (
                  <li key={key}>
                    <span>{capabilityLabel(key)}</span>
                    <code translate="no">{key}</code>
                  </li>
                ))}
              </ul>
            </Descriptions.Item>
          ) : null}
          <Descriptions.Item label="提交时间">
            <time dateTime={row.createdAt}>
              {formatDateTime(row.createdAt)}
            </time>
          </Descriptions.Item>
          <Descriptions.Item label="申请版本">
            v{row.revision}
          </Descriptions.Item>
        </Descriptions>
      </section>

      <section className={styles.detailBlock}>
        <header>
          <History aria-hidden="true" size={17} />
          <h3>审批记录</h3>
        </header>
        {row.decidedBy ? (
          <Descriptions column={1} size="small" colon={false}>
            <Descriptions.Item label="处理人">
              <code translate="no">{row.decidedBy}</code>
            </Descriptions.Item>
            <Descriptions.Item label="处理说明">
              {row.decisionReason || "未填写处理说明"}
            </Descriptions.Item>
            <Descriptions.Item label="更新时间">
              <time dateTime={row.updatedAt}>
                {formatDateTime(row.updatedAt)}
              </time>
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无审批记录"
          />
        )}
      </section>

      <details className={styles.technicalDetails}>
        <summary>查看技术标识</summary>
        <dl>
          <dt>申请 ID</dt>
          <dd>
            <code translate="no">{row.requestId}</code>
          </dd>
          <dt>申请人 ID</dt>
          <dd>
            <code translate="no">{row.requesterId}</code>
          </dd>
        </dl>
      </details>
    </div>
  );
}
