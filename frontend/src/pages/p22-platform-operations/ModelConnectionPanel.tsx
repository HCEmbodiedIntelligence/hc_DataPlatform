import { Alert, Button, Form, Input, Space } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  modelRequest,
  useModelScope,
  type Connection,
  type ModelScope,
} from "../../features/model-annotation/api";
import { useScopeQueryRevision } from "../../shared/api/use-scoped-query";
import styles from "./styles.module.css";

const example = { endpoint: "", model: "", sample_frames: 16, camera: null };

export function ModelConnectionPanel() {
  const scope = useModelScope();
  const revision = useScopeQueryRevision();
  return (
    <section className={styles.dataPanel} id="model-connection">
      <h3>模型标注配置</h3>
      {scope ? (
        <ConnectionForm
          key={JSON.stringify([scope, revision])}
          scope={scope}
          session={revision}
        />
      ) : (
        <p>选择项目和区域后可配置模型。</p>
      )}
    </section>
  );
}

function ConnectionForm({
  scope,
  session,
}: {
  scope: ModelScope;
  session: string;
}) {
  const client = useQueryClient();
  const key = ["model-connection", session, scope];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      modelRequest<Connection>(scope, "/connection", { method: "GET", signal }),
    retry: false,
  });
  const [edit, setEdit] = useState<{ json: string; revision: number } | null>(
    null,
  );
  const [secret, setSecret] = useState("");
  const [fileError, setFileError] = useState("");
  const test = useMutation({
    mutationFn: () =>
      modelRequest<{ model: string }>(scope, "/connection/test", {
        method: "POST",
      }),
  });
  const save = useMutation({
    mutationFn: () => {
      if (!edit) throw new Error("请先编辑配置");
      let config;
      try {
        config = JSON.parse(edit.json);
      } catch {
        throw new Error("请输入有效的 JSON 配置");
      }
      return modelRequest<Connection>(scope, "/connection", {
        method: "PUT",
        body: {
          revision: edit.revision,
          config,
          ...(secret ? { api_key: secret } : {}),
        },
      });
    },
    onSuccess: (data) => {
      client.setQueryData(key, data);
      setSecret("");
      setEdit(null);
      test.reset();
    },
  });
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      {query.error && (
        <Alert
          type="error"
          title={query.error.message}
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      )}
      {query.data && !edit && (
        <>
          <p>
            {query.data.config
              ? `当前项目模型 · ${query.data.config.model}（API Key 已配置）`
              : "当前项目尚未配置模型。"}
          </p>
          <Space wrap>
            <Button
              onClick={() => {
                save.reset();
                test.reset();
                setFileError("");
                setEdit({
                  json: JSON.stringify(query.data.config ?? example, null, 2),
                  revision: query.data.revision,
                });
              }}
            >
              编辑模型配置
            </Button>
            {query.data.config && (
              <a
                download="model-config.json"
                href={
                  "data:application/json;charset=utf-8," +
                  encodeURIComponent(JSON.stringify(query.data.config, null, 2))
                }
              >
                下载配置文件
              </a>
            )}
            {query.data.config && (
              <Button loading={test.isPending} onClick={() => test.mutate()}>
                测试模型调用
              </Button>
            )}
          </Space>
        </>
      )}
      {edit && (
        <Form
          layout="vertical"
          onFinish={() => save.mutate()}
          disabled={save.isPending}
        >
          <Form.Item label="导入配置文件" htmlFor="model-config-file">
            <input
              id="model-config-file"
              type="file"
              accept=".json,application/json"
              disabled={save.isPending}
              onChange={async (e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                if (!file) return;
                try {
                  if (file.size > 65536)
                    throw new Error("配置文件不能超过 64 KB");
                  const config = JSON.parse(await file.text());
                  if (
                    !config ||
                    typeof config !== "object" ||
                    Array.isArray(config) ||
                    Object.keys(config).some(
                      (key) => !Object.hasOwn(example, key),
                    )
                  ) {
                    throw new Error(
                      "配置文件仅支持 endpoint、model、sample_frames 和 camera，API Key 请单独填写",
                    );
                  }
                  setEdit((current) =>
                    current === edit
                      ? { ...current, json: JSON.stringify(config, null, 2) }
                      : current,
                  );
                  setFileError("");
                } catch (error) {
                  setFileError(
                    error instanceof SyntaxError
                      ? "配置文件不是有效的 JSON"
                      : error instanceof Error
                        ? error.message
                        : "读取配置文件失败",
                  );
                }
              }}
            />
            {fileError && <Alert type="error" title={fileError} />}
          </Form.Item>
          <Form.Item
            label="模型配置（JSON）"
            htmlFor="model-config-json"
            extra="endpoint 填写支持图片输入的 Chat Completions 完整 HTTPS 地址。camera 留空时自动选择 RGB 相机。"
          >
            <Input.TextArea
              id="model-config-json"
              rows={7}
              value={edit.json}
              onChange={(e) => setEdit({ ...edit, json: e.target.value })}
            />
          </Form.Item>
          <Form.Item label="API Key" htmlFor="model-api-key">
            <Input.Password
              id="model-api-key"
              value={secret}
              autoComplete="new-password"
              placeholder={
                edit.revision ? "留空沿用已保存的密钥" : "输入 API Key"
              }
              onChange={(e) => setSecret(e.target.value)}
            />
          </Form.Item>
          <Space>
            <Button htmlType="submit" type="primary" loading={save.isPending}>
              保存模型配置
            </Button>
            <Button
              onClick={() => {
                setSecret("");
                setEdit(null);
                save.reset();
              }}
            >
              取消
            </Button>
          </Space>
        </Form>
      )}
      {save.error && <Alert type="error" title={save.error.message} />}
      {save.isSuccess && <Alert type="success" title="模型配置已保存" />}
      {test.error && <Alert type="error" title={test.error.message} />}
      {test.isSuccess && (
        <Alert type="success" title={`模型 ${test.data.model} 已响应`} />
      )}
    </Space>
  );
}
