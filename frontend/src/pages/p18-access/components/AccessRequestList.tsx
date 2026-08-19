import { Button } from "antd";
import { KeyRound, UserRoundPlus } from "lucide-react";
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
  readonly onSelect: (row: AccessRequestRow) => void;
  readonly selectedTriggerRef: RefObject<HTMLButtonElement | null>;
}

export function AccessRequestList({
  onSelect,
  rows,
  selectedId,
  selectedTriggerRef,
}: Readonly<AccessRequestListProps>) {
  return (
    <ol className={styles.requestList} aria-label="申请列表">
      {rows.map((row) => {
        const selected = row.requestId === selectedId;
        const Icon = row.kind === "membership" ? UserRoundPlus : KeyRound;
        return (
          <li key={row.requestId}>
            <Button
              ref={selected ? selectedTriggerRef : undefined}
              className={styles.requestCard}
              data-selected={selected || undefined}
              type="text"
              aria-pressed={selected}
              onClick={() => onSelect(row)}
            >
              <span className={styles.requestCardTopline}>
                <span
                  className={
                    row.kind === "membership"
                      ? styles.kindMembership
                      : styles.kindCapability
                  }
                >
                  <Icon aria-hidden="true" size={14} />
                  {row.kind === "membership" ? "加入项目" : "权限申请"}
                </span>
                <StatusTag
                  status={row.status}
                  label={statusLabel(row.status)}
                  tone={statusTone(row.status)}
                />
              </span>
              <span className={styles.requestIdentity} title={row.requesterId}>
                {shortIdentity(row.requesterId)}
              </span>
              <span className={styles.requestMeta}>
                <span>目标项目</span>
                <strong title={row.projectId}>
                  {shortIdentity(row.projectId)}
                </strong>
              </span>
              <span className={styles.requestMeta}>
                <span>
                  {row.kind === "membership" ? "申请说明" : "请求权限"}
                </span>
                <strong title={accessRequestSummary(row)}>
                  {accessRequestSummary(row)}
                </strong>
              </span>
              <span className={styles.requestMeta}>
                <span>提交时间</span>
                <time dateTime={row.createdAt}>
                  {formatDateTime(row.createdAt)}
                </time>
              </span>
            </Button>
          </li>
        );
      })}
    </ol>
  );
}
