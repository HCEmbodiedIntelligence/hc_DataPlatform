import { Descriptions } from "antd";
import { TriangleAlert } from "lucide-react";
import type { AccessRequestRow } from "../contracts";
import {
  capabilityLabel,
  elevatedImpactNotes,
  formatDateTime,
} from "../presentation";
import styles from "../styles.module.css";

export function AccessRequestDetails({
  row,
}: {
  readonly row: AccessRequestRow;
}) {
  const processed = row.status !== "PENDING";
  const impacts = elevatedImpactNotes(row);

  return (
    <div className={styles.drawerDetails}>
      <section
        className={styles.detailSection}
        aria-labelledby="request-core-heading"
      >
        <h3 id="request-core-heading">核心信息</h3>
        <Descriptions column={1} size="small" colon={false}>
          <Descriptions.Item label="申请人">
            <code className={styles.identityValue} translate="no">
              {row.requesterId}
            </code>
          </Descriptions.Item>
          <Descriptions.Item label="申请原因">
            {row.reason || "未填写申请原因"}
          </Descriptions.Item>
          <Descriptions.Item label="提交时间">
            <time dateTime={row.createdAt}>
              {formatDateTime(row.createdAt)}
            </time>
          </Descriptions.Item>
        </Descriptions>
      </section>

      {row.kind === "capability" ? (
        <section
          className={styles.detailSection}
          aria-labelledby="request-capability-heading"
        >
          <h3 id="request-capability-heading">申请能力</h3>
          <ul className={styles.capabilityList}>
            {row.capabilityKeys.map((key) => {
              const label = capabilityLabel(key);
              return (
                <li key={key}>
                  {label === key ? null : <span>{label}</span>}
                  <code translate="no">{key}</code>
                </li>
              );
            })}
          </ul>
          {impacts.length > 0 ? (
            <div
              className={styles.impactNote}
              role="note"
              aria-label="影响提示"
            >
              <TriangleAlert aria-hidden="true" size={17} />
              <div>
                <strong>影响提示</strong>
                <ul className={styles.impactList}>
                  {impacts.map((impact) => (
                    <li key={impact}>{impact}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {processed ? (
        <section
          className={styles.detailSection}
          aria-labelledby="request-decision-record-heading"
        >
          <h3 id="request-decision-record-heading">处理记录</h3>
          <Descriptions column={1} size="small" colon={false}>
            <Descriptions.Item label="处理人">
              {row.decidedBy ? (
                <code translate="no">{row.decidedBy}</code>
              ) : row.status === "WITHDRAWN" ? (
                "申请人本人"
              ) : (
                "未记录"
              )}
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
        </section>
      ) : null}

      <details className={styles.technicalDetails}>
        <summary>技术信息</summary>
        <dl>
          <dt>申请 ID</dt>
          <dd>
            <code translate="no">{row.requestId}</code>
          </dd>
          <dt>修订版本</dt>
          <dd>v{row.revision}</dd>
          <dt>项目 ID</dt>
          <dd>
            <code translate="no">{row.projectId}</code>
          </dd>
        </dl>
      </details>
    </div>
  );
}
