import { Alert, Button, Form, Select, Space } from "antd";
import { useEffect, type ReactNode } from "react";
import { useCaptureQuery, captureApi } from "./api";
import { versionReference, type SetTaskPlan } from "./contracts";
import { captureError } from "./presentation";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";

export interface TaskPlanSelection {
  schemeId?: string;
  versionKey: string;
}

export function TaskPlanSelector({
  organizationId,
  value,
  onSelectionChange,
  onChange,
  disabled = false,
  selectedName,
  action,
}: {
  organizationId: string;
  value: TaskPlanSelection;
  onSelectionChange: (value: TaskPlanSelection) => void;
  onChange: (plan: SetTaskPlan | null) => void;
  disabled?: boolean;
  selectedName?: string;
  action?: ReactNode;
}) {
  const permissions = useOrganizationCapabilities(organizationId);
  const canRead = permissions.has("robot.read");
  // Restore the scheme from drafts saved by the previous task form as well.
  const schemeId = value.schemeId || value.versionKey.split(":")[0];
  const list = useCaptureQuery(
    organizationId,
    "list",
    { include_archived: false },
    (signal) =>
      captureApi.list(organizationId, { include_archived: false }, signal),
    canRead,
  );
  const source = useCaptureQuery(
    organizationId,
    "detail",
    schemeId,
    (signal) => captureApi.detail(organizationId, schemeId!, signal),
    canRead && Boolean(schemeId),
  );
  const available = (list.data ?? []).filter(
    (scheme) => !scheme.archived && scheme.latest_version !== null,
  );
  const detail = source.data?.scheme_id === schemeId ? source.data : undefined;
  const keyOf = (version: {
    scheme_id: string;
    version: number;
    content_sha256: string;
  }) => `${version.scheme_id}:${version.version}:${version.content_sha256}`;
  const chosen = detail?.versions.find(
    (version) => keyOf(version) === value.versionKey,
  );
  const options = available.map((scheme) => ({
    value: scheme.scheme_id,
    label:
      scheme.scheme_id === schemeId
        ? (chosen?.name ?? selectedName ?? scheme.draft.name)
        : scheme.draft.name,
    disabled: false,
  }));
  if (schemeId && !options.some((option) => option.value === schemeId)) {
    options.push({
      value: schemeId,
      label: chosen?.name ?? selectedName ?? detail?.draft.name ?? "读取中…",
      disabled: true,
    });
  }
  const ready =
    canRead &&
    list.isSuccess &&
    !list.isFetching &&
    !source.isFetching &&
    !source.error &&
    detail &&
    !detail.archived &&
    available.some((scheme) => scheme.scheme_id === schemeId);
  const plan: SetTaskPlan | null =
    ready && chosen
      ? { expected_revision: 0, scheme: versionReference(chosen) }
      : null;
  const planIdentity = JSON.stringify(plan);
  useEffect(() => {
    onChange(plan);
  }, [planIdentity, onChange]);
  useEffect(() => {
    if (!ready || value.versionKey || !detail) return;
    const latest = detail.versions.find(
      (version) => version.version === detail.latest_version,
    );
    if (latest) onSelectionChange({ schemeId, versionKey: keyOf(latest) });
  }, [ready, detail, schemeId, value.versionKey]);

  return (
    <section aria-label="任务采集计划">
      {!canRead && (
        <Alert type="warning" title="选择采集方案需要组织机器人读取权限" />
      )}
      {(list.error || source.error) && (
        <Alert
          type="error"
          title={captureError(list.error ?? source.error)}
          action={
            <Button
              onClick={() => {
                void list.refetch();
                if (schemeId) void source.refetch();
              }}
            >
              重试
            </Button>
          }
        />
      )}
      <Form.Item label="采集方案" required>
        <Space.Compact block>
          <Select
            aria-label="任务采集方案"
            placeholder="选择采集方案"
            style={{ flex: 1, minWidth: 0 }}
            disabled={disabled || !canRead}
            showSearch={{ optionFilterProp: "label" }}
            loading={list.isFetching || source.isFetching}
            value={schemeId || undefined}
            options={options}
            onSelect={(id: string) =>
              onSelectionChange({ schemeId: id, versionKey: "" })
            }
          />
          {action}
        </Space.Compact>
      </Form.Item>
      {canRead &&
        list.isSuccess &&
        !list.isFetching &&
        available.length === 0 && (
          <Alert
            type="info"
            title="暂无已发布的采集方案，请先创建并发布方案。"
          />
        )}
      {ready && value.versionKey && !chosen && (
        <Alert
          type="warning"
          title="已选方案版本不可用，请重新选择采集方案。"
        />
      )}
      {detail?.archived && (
        <Alert type="warning" title="已选方案已归档，请选择其他采集方案。" />
      )}
    </section>
  );
}
