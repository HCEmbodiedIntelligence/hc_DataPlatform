import { useRef, useState } from "react";
import { Alert, Button, Dropdown, Input, Modal, Radio, Space } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { request } from "../../shared/api/http-client";
import { getSessionBootstrap } from "../../features/account/session/api";
import { installSessionBootstrap } from "../../features/account/session/runtime-scope";
import { accountAccessKeys } from "../../features/account/access-api";
import { useShellStore } from "../../shared/scope/shell-store";

type Operation =
  | "rename"
  | "generate_code"
  | "revoke_code"
  | "archive"
  | "delete";
type Target = { organization_id: string; project_id?: string };
type Change = Target & {
  operation: Operation;
  name?: string;
  confirmation?: string;
  retention_days?: number;
};

function useRefreshDirectory() {
  const client = useQueryClient();
  const actor = useShellStore((s) => s.principal?.actorId);
  return async () => {
    if (!actor || useShellStore.getState().principal?.actorId !== actor) return;
    await Promise.all(
      [
        ["management-directory"],
        ["archived-directory"],
        accountAccessKeys.overview(actor),
        ["management-members", actor],
        ["management-inbox", actor],
      ].map((queryKey) => client.invalidateQueries({ queryKey })),
    );
    const session = await getSessionBootstrap();
    if (useShellStore.getState().principal?.actorId === actor)
      installSessionBootstrap(session);
  };
}

export function CreateOrganizationButton({
  onCreated,
}: {
  onCreated?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const key = useRef("");
  const refresh = useRefreshDirectory();
  const create = useMutation({
    mutationFn: () =>
      request<{ organization_id: string }>({
        method: "POST",
        path: "/account/organizations",
        scopeMode: "session",
        idempotencyKey: key.current,
        body: { name: name.trim() },
      }),
    onSuccess: async (result) => {
      setOpen(false);
      setName("");
      onCreated?.(result.organization_id);
      await refresh();
    },
  });
  return (
    <>
      <Button
        onClick={() => {
          create.reset();
          key.current = crypto.randomUUID();
          setOpen(true);
        }}
      >
        创建组织
      </Button>
      <Modal
        open={open}
        title="创建组织"
        okText="创建"
        cancelText="取消"
        confirmLoading={create.isPending}
        okButtonProps={{ disabled: !name.trim() }}
        onCancel={() => {
          if (!create.isPending) setOpen(false);
        }}
        onOk={() => create.mutate()}
      >
        <label>
          组织名称
          <Input
            autoFocus
            maxLength={256}
            value={name}
            onChange={(e) => {
              key.current = crypto.randomUUID();
              setName(e.target.value);
            }}
          />
        </label>
        <p>创建后，你将成为该组织的管理员。</p>
        {create.isError && (
          <Alert type="error" message={create.error.message} />
        )}
      </Modal>
    </>
  );
}
export function DirectoryActions({
  target,
  name,
  mode,
  hasCode = false,
  canRetire = true,
}: {
  target: Target;
  name: string;
  mode: "identity" | "code" | "archived";
  hasCode?: boolean;
  canRetire?: boolean;
}) {
  const label = target.project_id ? "项目" : "组织";
  const identifier = target.project_id ?? target.organization_id;
  const [operation, setOperation] = useState<Operation>();
  const [value, setValue] = useState("");
  const [days, setDays] = useState<0 | 30>(30);
  const key = useRef("");
  const refresh = useRefreshDirectory();
  const change = useMutation({
    mutationFn: (body: Change) =>
      request({
        method: "POST",
        path: "/account/directory-changes",
        scopeMode: "session",
        body,
        idempotencyKey: key.current,
      }),
    onSuccess: async () => {
      setOperation(undefined);
      await refresh();
    },
  });
  const begin = (next: Operation) => {
    key.current = crypto.randomUUID();
    change.reset();
    if (next === "generate_code") {
      change.mutate({ ...target, operation: next });
      return;
    }
    setDays(30);
    setValue(next === "rename" ? name : "");
    setOperation(next);
  };
  const titles = {
    rename: "修改" + label + "名称",
    generate_code: "生成加入码",
    revoke_code: "停用加入码",
    archive: "归档" + label,
    delete: "永久删除" + label,
  };
  const destructive = operation === "delete" || operation === "archive";
  return (
    <Space wrap>
      {mode === "code" ? (
        <>
          <Button
            loading={change.isPending && !operation}
            onClick={() => begin("generate_code")}
          >
            {hasCode ? "重新生成" : "生成加入码"}
          </Button>
          {hasCode && (
            <Button
              disabled={change.isPending}
              onClick={() => begin("revoke_code")}
            >
              停用加入码
            </Button>
          )}
        </>
      ) : mode === "archived" ? (
        <Button danger onClick={() => begin("delete")}>
          永久删除
        </Button>
      ) : (
        <Dropdown
          trigger={["click"]}
          menu={{
            items: [
              { key: "rename", label: titles.rename },
              ...(canRetire
                ? [
                    { key: "archive", label: titles.archive },
                    { key: "delete", label: titles.delete, danger: true },
                  ]
                : []),
            ],
            onClick: ({ key: action }) => begin(action as Operation),
          }}
        >
          <Button>{label}操作</Button>
        </Dropdown>
      )}
      {!operation && change.isError && (
        <Alert type="error" message={change.error.message} />
      )}
      <Modal
        open={!!operation}
        title={operation && titles[operation]}
        okText={operation && titles[operation]}
        cancelText="取消"
        confirmLoading={change.isPending}
        okButtonProps={{
          danger: operation === "delete",
          disabled:
            operation === "rename"
              ? !value.trim()
              : destructive && value !== identifier,
        }}
        onCancel={() => {
          if (!change.isPending) setOperation(undefined);
        }}
        onOk={() => {
          if (operation)
            change.mutate({
              ...target,
              operation,
              ...(operation === "rename"
                ? { name: value.trim() }
                : destructive
                  ? { confirmation: value }
                  : {}),
              ...(operation === "delete" ? { retention_days: days } : {}),
            });
        }}
      >
        {operation === "rename" && (
          <label>
            {label}名称
            <Input
              autoFocus
              maxLength={256}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        )}
        {operation === "revoke_code" && (
          <p>停用后，当前加入码立即失效。你可以随时生成新码。</p>
        )}
        {destructive && (
          <>
            <p>
              {operation === "archive"
                ? "归档后立即停止访问，保留数据。"
                : "删除后立即关闭访问，移除成员关系。"}
              {label === "组织" ? "此操作包括下属项目。" : ""}用户账号保留。
            </p>
            {operation === "delete" && (
              <Radio.Group
                value={days}
                onChange={(e) => setDays(e.target.value)}
                options={[
                  { label: "保留数据 30 天后删除", value: 30 },
                  { label: "立即删除数据", value: 0 },
                ]}
              />
            )}
            <label>
              输入{label}编号 <strong>{identifier}</strong> 确认
              <Input
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </label>
          </>
        )}
        {change.isError && (
          <Alert type="error" message={change.error.message} />
        )}
      </Modal>
    </Space>
  );
}
