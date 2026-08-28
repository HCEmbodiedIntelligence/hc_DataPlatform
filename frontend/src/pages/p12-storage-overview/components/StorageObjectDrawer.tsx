import { Button, Descriptions } from "antd";
import type { RefObject } from "react";
import type { StorageInventoryFact } from "../../../entities/storage-inventory";
import { formatByteString } from "../../../features/storage-overview/metrics-contract";
import {
  EntityDrawer,
  PageState,
  type PageStateKind,
} from "../../../shared/ui";
import { objectRoleLabel, protectionReasonLabel } from "../display-labels";

const descriptionStyles = {
  label: { color: "var(--hc-color-text)" },
} as const;

const objectRoleStyle = {
  color: "var(--hc-color-text)",
  fontWeight: 600,
} as const;

export function StorageObjectDrawer({
  open,
  state,
  object,
  requestId,
  onRetry,
  onClose,
  returnFocusRef,
}: Readonly<{
  open: boolean;
  state: PageStateKind | "ready";
  object?: StorageInventoryFact;
  requestId?: string | null;
  onRetry?: () => void;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}>) {
  return (
    <EntityDrawer
      open={open}
      title="对象详情"
      onClose={onClose}
      returnFocusRef={returnFocusRef}
      loading={state === "loading"}
      extra={
        <Button aria-label="关闭对象详情" onClick={onClose}>
          关闭
        </Button>
      }
    >
      {state !== "ready" && state !== "loading" ? (
        <PageState
          state={state}
          label="对象详情状态"
          requestId={requestId}
          onRetry={onRetry}
        />
      ) : object ? (
        <Descriptions size="small" column={1} styles={descriptionStyles}>
          <Descriptions.Item label="对象">
            {object.displayKey}
          </Descriptions.Item>
          <Descriptions.Item label="对象 ID">
            <code>{object.objectId}</code>
          </Descriptions.Item>
          <Descriptions.Item label="角色">
            <span style={objectRoleStyle}>
              {objectRoleLabel(object.objectRole)}
            </span>
          </Descriptions.Item>
          <Descriptions.Item label="占用空间">
            {formatByteString(object.physicalBytes)}
          </Descriptions.Item>
          <Descriptions.Item label="保护原因">
            {object.protectionReasons.map(protectionReasonLabel).join("、") ||
              "无"}
          </Descriptions.Item>
        </Descriptions>
      ) : null}
    </EntityDrawer>
  );
}
