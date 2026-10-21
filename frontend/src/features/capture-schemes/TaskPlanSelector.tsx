import { Alert, Button, Form, Select, Space } from "antd";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useCaptureQuery, captureApi } from "./api";
import { versionReference, type SetTaskPlan } from "./contracts";
import { captureError, dataSummary } from "./presentation";
import { schemeLibraryPath } from "./navigation";
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
}: {
  organizationId: string;
  value: TaskPlanSelection;
  onSelectionChange: (value: TaskPlanSelection) => void;
  onChange: (plan: SetTaskPlan | null) => void;
  disabled?: boolean;
}) {
  const location = useLocation();
  const back = location.pathname + location.search;
  const permissions = useOrganizationCapabilities(organizationId);
  const canRead = permissions.has("robot.read");
  const [showVersions, setShowVersions] = useState(false);
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
      <h3>采集方案</h3>
      <p>使用同一采集方案的机器人均可执行此任务并上传数据。</p>
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
        <Select
          aria-label="任务采集方案"
          placeholder="选择采集方案"
          disabled={disabled || !canRead}
          showSearch={{ optionFilterProp: "label" }}
          loading={list.isFetching || source.isFetching}
          value={schemeId || undefined}
          options={available.map((scheme) => ({
            value: scheme.scheme_id,
            label: scheme.draft.name,
          }))}
          onChange={(id: string) => {
            setShowVersions(false);
            onSelectionChange({ schemeId: id, versionKey: "" });
          }}
        />
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
      {chosen && (
        <>
          <Space wrap>
            <span>
              已选 {chosen.name} · v{chosen.version}
            </span>
            {(detail?.versions.length ?? 0) > 1 && !showVersions && (
              <Button
                type="link"
                size="small"
                disabled={disabled}
                onClick={() => setShowVersions(true)}
              >
                更换版本
              </Button>
            )}
          </Space>
          {showVersions && (
            <Form.Item label="方案版本">
              <Select
                aria-label="任务采集方案版本"
                disabled={disabled || !ready}
                value={value.versionKey}
                options={detail?.versions.map((version) => ({
                  value: keyOf(version),
                  label: `v${version.version}${version.version === detail.latest_version ? "（最新发布）" : ""}`,
                }))}
                onChange={(versionKey) =>
                  onSelectionChange({ schemeId, versionKey })
                }
              />
            </Form.Item>
          )}
          <p>{dataSummary(chosen)}</p>
        </>
      )}
      <p>任务保存所选方案版本；开始采集后固定，后续发布新版不会影响本任务。</p>
      <Space wrap>
        <Link to={schemeLibraryPath(back)}>查看方案库</Link>
        {permissions.has("robot.manage") && (
          <Link to={schemeLibraryPath(back, undefined, true)}>
            新建方案并返回
          </Link>
        )}
      </Space>
    </section>
  );
}
