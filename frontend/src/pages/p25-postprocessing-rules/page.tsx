import { Alert, Button, Table } from "antd";
import { Link } from "react-router-dom";
import {
  captureApi,
  useCaptureQuery,
} from "../../features/capture-schemes/api";
import { schemeLibraryPath } from "../../features/capture-schemes/navigation";
import { captureError } from "../../features/capture-schemes/presentation";
import type { SchemeSummary } from "../../features/capture-schemes/contracts";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { useCurrentOrganizationId } from "../../shared/scope/current-organization";
import { StandardPageScaffold } from "../../shared/ui";

const path = "/settings/postprocessing-rules";

function RulesList({ org }: { org: string }) {
  const capabilities = useOrganizationCapabilities(org);
  const read = capabilities.has("robot.read");
  const manage = capabilities.has("robot.manage");
  const filter = { source_format: "MCAP", include_archived: false };
  const list = useCaptureQuery(
    org,
    "list",
    filter,
    (signal) => captureApi.list(org, filter, signal),
    read,
  );
  if (!read)
    return <Alert type="warning" title="当前组织没有采集方案读取权限" />;
  if (list.isError)
    return (
      <Alert
        type="error"
        title={captureError(list.error)}
        action={<Button onClick={() => void list.refetch()}>重新加载</Button>}
      />
    );
  const supported = (list.data ?? []).filter((item) =>
    item.draft.adapters.some(
      (a) => a.source_format === "MCAP" && a.configuration.normalization,
    ),
  );
  return (
    <>
      <p>
        选择采集方案，查看或编辑质检前执行的规则。此处显示方案草稿，验证并发布后可供任务选用。
      </p>
      <Table<SchemeSummary>
        rowKey="scheme_id"
        dataSource={supported}
        loading={list.isPending}
        locale={{ emptyText: "当前组织没有启用 HC TJ 17 轴归一化的 MCAP 方案" }}
        pagination={{ pageSize: 12, hideOnSinglePage: true }}
        columns={[
          {
            title: "采集方案",
            key: "name",
            render: (_, item) => (
              <Link to={schemeLibraryPath(path, item.scheme_id)}>
                {item.draft.name}
              </Link>
            ),
          },
          {
            title: "已发布版本",
            key: "version",
            render: (_, item) =>
              item.latest_version == null
                ? "尚未发布"
                : `v${item.latest_version}`,
          },
          {
            title: "草稿中的动作补值",
            key: "rules",
            render: (_, item) => {
              const rules = item.draft.adapters.find(
                (a) => a.source_format === "MCAP",
              )?.configuration.postprocessing_rules;
              if (!rules) return "内置规则 · 腰部缺失指令使用 20 毫秒内的状态";
              return (
                <>
                  <div>
                    关节缺失：
                    {rules.missing_position_axes.join("、") || "不补值"} · NaN：
                    {rules.nan_position_axes.join("、") || "不补值"}
                  </div>
                  <div>
                    时间容差 {rules.max_state_delta_ms} 毫秒 · 主动作话题为空时
                    {rules.missing_action_topic === "copy_state"
                      ? "按状态生成"
                      : "报错"}
                  </div>
                </>
              );
            },
          },
          {
            title: "操作",
            key: "edit",
            render: (_, item) =>
              manage ? (
                <Link
                  to={`${schemeLibraryPath(path, item.scheme_id)}&edit=1&section=postprocessing`}
                >
                  编辑规则
                </Link>
              ) : (
                "只读"
              ),
          },
        ]}
      />
    </>
  );
}

export function Component() {
  const org = useCurrentOrganizationId();
  return (
    <StandardPageScaffold
      header={{
        title: "数据后处理规则",
        breadcrumbs: [
          { key: "governance", label: "治理与资产" },
          { key: "rules", label: "数据后处理规则" },
        ],
      }}
    >
      {org ? (
        <RulesList key={org} org={org} />
      ) : (
        <Alert type="info" title="请先选择组织" />
      )}
    </StandardPageScaffold>
  );
}
