import { Alert, Button, Form, Input, Space, Table } from "antd";
import { useState } from "react";
import {
  useCreateLibraryTag,
  useLibraryTags,
} from "../../features/capture-schemes/api";
import { captureError } from "../../features/capture-schemes/presentation";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { useCurrentOrganizationId } from "../../shared/scope/current-organization";
import { StandardPageScaffold } from "../../shared/ui";

export function Component() {
  const org = useCurrentOrganizationId();
  return org ? (
    <Library key={org} org={org} />
  ) : (
    <Alert type="info" title="请先选择组织" />
  );
}

function Library({ org }: { org: string }) {
  const rights = useOrganizationCapabilities(org);
  const list = useLibraryTags(org, rights.has("robot.read"));
  const create = useCreateLibraryTag(org);
  const [name, setName] = useState("");
  const duplicate = list.data?.includes(name.trim());
  return (
    <StandardPageScaffold
      header={{
        title: "标签库",
        breadcrumbs: [
          { key: "governance", label: "治理与资产" },
          { key: "tags", label: "标签库" },
        ],
        description:
          "在采集方案中选用这里的标签，供标注员标记任务中的数据片段。",
      }}
    >
      {rights.has("tag.manage") && (
        <Form
          layout="inline"
          onFinish={() => {
            if (name.trim() && !duplicate && !create.isPending) {
              create.mutate(name.trim(), { onSuccess: () => setName("") });
            }
          }}
        >
          <Form.Item
            label="标签名称"
            htmlFor="library-tag-name"
            help={duplicate ? "该标签已在库中" : undefined}
          >
            <Input
              id="library-tag-name"
              maxLength={100}
              value={name}
              disabled={create.isPending}
              onChange={(e) => {
                setName(e.target.value);
                create.reset();
              }}
            />
          </Form.Item>
          <Button
            htmlType="submit"
            type="primary"
            loading={create.isPending}
            disabled={!name.trim() || duplicate}
          >
            创建标签
          </Button>
        </Form>
      )}
      <Space orientation="vertical" style={{ width: "100%", marginTop: 16 }}>
        {create.error && (
          <Alert type="error" title={captureError(create.error)} />
        )}
        {create.isSuccess && (
          <Alert type="success" title={`标签「${create.data.name}」已保存`} />
        )}
        {list.error && (
          <Alert
            type="error"
            title={captureError(list.error)}
            action={<Button onClick={() => void list.refetch()}>重试</Button>}
          />
        )}
        <Table
          rowKey="name"
          loading={list.isFetching}
          dataSource={list.data?.map((name) => ({ name }))}
          columns={[{ title: "标签名称", dataIndex: "name" }]}
          locale={{ emptyText: list.error ? "标签读取失败" : "标签库为空" }}
        />
      </Space>
    </StandardPageScaffold>
  );
}
