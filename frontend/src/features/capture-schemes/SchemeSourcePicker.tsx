import { Alert, Button, Form, Select } from "antd";
import { useState } from "react";
import { captureApi, useCaptureQuery } from "./api";
import { documentSchema, type SchemeDocument } from "./contracts";
import { captureError } from "./presentation";
import styles from "./definition.module.css";

type SourcePickerProps = {
  organizationId: string;
  onLoad: (document: SchemeDocument) => void;
};

export function SchemeSourcePicker(props: SourcePickerProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details
      className={styles.source}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>从已有方案复制配置（可选）</summary>
      {expanded && <SchemeSourceFields {...props} />}
    </details>
  );
}

function SchemeSourceFields({ organizationId, onLoad }: SourcePickerProps) {
  const [schemeId, setSchemeId] = useState<string>();
  const [selectedVersion, setSelectedVersion] = useState<number | "draft">();
  const [loadedFrom, setLoadedFrom] = useState("");
  const filter = { include_archived: true };
  const list = useCaptureQuery(organizationId, "list", filter, (signal) =>
    captureApi.list(organizationId, filter, signal),
  );
  const source = useCaptureQuery(
    organizationId,
    "detail",
    schemeId,
    (signal) => captureApi.detail(organizationId, schemeId!, signal),
    Boolean(schemeId),
  );
  const detail = source.data?.scheme_id === schemeId ? source.data : undefined;
  const version = selectedVersion ?? detail?.latest_version ?? "draft";
  const document =
    version === "draft"
      ? detail?.draft
      : detail?.versions.find((item) => item.version === version);

  return (
    <div className={styles.sourceBody}>
      <p>
        复用已有方案的数据定义和原始文件配置，再按需要修改。保留第一步填写的名称、说明和适用型号。
      </p>
      {list.error && (
        <Alert
          type="error"
          title={`已有方案读取失败：${captureError(list.error)}`}
          action={<Button onClick={() => void list.refetch()}>重试</Button>}
        />
      )}
      {list.isSuccess && list.data.length === 0 && (
        <Alert
          type="info"
          title="还没有可复用的方案，可以直接填写下方数据定义。"
        />
      )}
      <div className={styles.grid}>
        <Form.Item label="已有方案">
          <Select
            aria-label="已有方案"
            placeholder="搜索并选择已有方案"
            showSearch={{ optionFilterProp: "label" }}
            allowClear
            loading={list.isFetching}
            value={schemeId}
            options={(list.data ?? []).map((item) => ({
              value: item.scheme_id,
              label: `${item.draft.name}${item.archived ? "（已归档）" : ""}`,
            }))}
            onChange={(value: string | undefined) => {
              setSchemeId(value);
              setSelectedVersion(undefined);
            }}
          />
        </Form.Item>
        <Form.Item label="来源版本">
          <Select<number | "draft">
            aria-label="来源版本"
            placeholder="先选择已有方案"
            disabled={!detail || source.isError}
            loading={Boolean(schemeId) && source.isFetching}
            value={detail ? version : undefined}
            options={
              detail
                ? [
                    ...detail.versions.map((item) => ({
                      value: item.version,
                      label: `v${item.version}${item.version === detail.latest_version ? "（最新发布）" : ""}`,
                    })),
                    { value: "draft", label: "当前已保存草稿" },
                  ]
                : []
            }
            onChange={setSelectedVersion}
          />
        </Form.Item>
      </div>
      {source.error && (
        <Alert
          type="error"
          title={`方案配置读取失败：${captureError(source.error)}`}
          action={
            <Button onClick={() => void source.refetch()}>重新读取</Button>
          }
        />
      )}
      <p>填入会替换当前数据定义和原始文件配置，修改后需要重新验证。</p>
      <Button
        type="primary"
        loading={Boolean(schemeId) && source.isFetching}
        disabled={
          !document || list.isError || source.isError || source.isFetching
        }
        onClick={() => {
          if (!document) return;
          onLoad(documentSchema.parse(document));
          setLoadedFrom(
            `${document.name} · ${version === "draft" ? "当前已保存草稿" : `v${version}`}`,
          );
        }}
      >
        填入配置
      </Button>
      {loadedFrom && (
        <Alert
          showIcon
          type="success"
          title={`已填入 ${loadedFrom}，可继续修改下方数据定义。`}
        />
      )}
    </div>
  );
}
