import { Button } from "antd";
import type { RefObject } from "react";
import { StatusTag } from "../../../shared/ui";
import type { AccessRequestRow } from "../contracts";
import {
  accessRequestSummary,
  formatDateTime,
  shortIdentity,
  statusLabel,
  statusTone,
} from "../presentation";
import styles from "../styles.module.css";

export interface AccessRequestListProps {
  readonly rows: readonly AccessRequestRow[];
  readonly selectedId?: string;
  readonly summaryLabel: "申请说明" | "权限摘要";
  readonly onSelect: (row: AccessRequestRow, trigger: HTMLElement) => void;
  readonly selectedTriggerRef: RefObject<HTMLButtonElement | null>;
}

export function AccessRequestList({
  onSelect,
  rows,
  selectedId,
  selectedTriggerRef,
  summaryLabel,
}: Readonly<AccessRequestListProps>) {
  return (
    <div className={styles.requestTableWrap}>
      <table className={styles.requestTable} aria-label="申请列表">
        <thead>
          <tr>
            <th scope="col">申请人</th>
            <th scope="col">{summaryLabel}</th>
            <th scope="col">状态</th>
            <th scope="col">提交时间</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const selected = row.requestId === selectedId;
            const summary = accessRequestSummary(row);
            const submittedAt = formatDateTime(row.createdAt);
            return (
              <tr key={row.requestId} data-selected={selected || undefined}>
                <td className={styles.requestApplicantCell} data-label="申请人">
                  <span title={row.requesterId}>
                    {shortIdentity(row.requesterId)}
                  </span>
                </td>
                <td
                  className={styles.requestSummaryCell}
                  data-label={summaryLabel}
                >
                  <span title={summary}>{summary}</span>
                </td>
                <td className={styles.requestStatusCell} data-label="状态">
                  <StatusTag
                    status={row.status}
                    label={statusLabel(row.status)}
                    tone={statusTone(row.status)}
                  />
                </td>
                <td className={styles.requestTimeCell} data-label="提交时间">
                  <time dateTime={row.createdAt} title={submittedAt}>
                    {submittedAt}
                  </time>
                </td>
                <td className={styles.requestActionCell} data-label="操作">
                  <Button
                    ref={selected ? selectedTriggerRef : undefined}
                    className={styles.requestViewButton}
                    type="link"
                    aria-label={`查看 ${row.requesterId} 的申请`}
                    onClick={(event) => onSelect(row, event.currentTarget)}
                  >
                    查看
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
