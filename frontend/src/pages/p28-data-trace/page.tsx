import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Empty,
  Input,
  Pagination,
  Select,
  Table,
  Tag,
} from "antd";
import { useSearchParams } from "react-router-dom";
import { useShellStore } from "../../shared/scope/shell-store";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { StandardPageScaffold } from "../../shared/ui";
import type { Scope } from "../../shared/scope/model";
import {
  suggestNames,
  findOrigins,
  loadMembers,
  pageSize,
  type Member,
} from "./api";
import { SampleCard } from "./SampleCard";
import { RawFiles } from "./RawFiles";
import styles from "./styles.module.css";

const rowKey = (row: Member) =>
  JSON.stringify([row.upload_id, row.data?.data_version_id]);
function Search({
  initial,
  scope,
  submit,
}: {
  initial: string;
  scope: Scope;
  submit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [typing, setTyping] = useState(false);
  const term = draft.trim();
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term), 200);
    return () => window.clearTimeout(timer);
  }, [term]);
  const ready = typing && !!term && term === debounced;
  const matches = useQuery({
    queryKey: ["trace-suggestions", scope, debounced],
    enabled: ready,
    queryFn: ({ signal }) => suggestNames(scope, debounced, signal),
  });
  const lookup = (value: string) => {
    setTyping(false);
    setDraft(value);
    if (value.trim()) submit(value.trim());
  };
  return (
    <form
      className={styles.search}
      onSubmit={(event) => {
        event.preventDefault();
        lookup(draft);
      }}
    >
      <label htmlFor="trace-identifier">文件名或 ID</label>
      <AutoComplete
        id="trace-identifier"
        value={draft}
        allowClear
        placeholder="输入导出文件名、部分名称或样本 ID"
        options={ready ? (matches.data ?? []).map((value) => ({ value })) : []}
        onChange={(value) => {
          setDraft(value);
          setTyping(true);
        }}
        onSelect={lookup}
        onFocus={() => setTyping(true)}
        onBlur={() => setTyping(false)}
      >
        <Input maxLength={256} />
      </AutoComplete>
      <Button htmlType="submit" type="primary" disabled={!term}>
        查找上传批次
      </Button>
    </form>
  );
}
export function Component() {
  const scope = useShellStore((s) => s.scope);
  const caps = useCapabilities();
  const [params, setParams] = useSearchParams();
  const id = params.get("id")?.trim() || "";
  const enabled = Boolean(scope?.projectId && scope.regionCode && id);
  const origins = useQuery({
    queryKey: ["trace-origins", scope, id],
    enabled,
    queryFn: ({ signal }) => findOrigins(scope!, id, signal),
  });
  const related = useQuery({
    queryKey: ["trace-suggestions", scope, id],
    enabled: enabled && origins.data?.length === 0,
    queryFn: ({ signal }) => suggestNames(scope!, id, signal),
  });
  const choices = origins.data ?? [];
  const originKey = (choice: (typeof choices)[number]) =>
    JSON.stringify([choice.identifier, !!choice.data]);
  const requested = choices.findIndex(
    (s) => originKey(s) === params.get("sample"),
  );
  const seed =
    choices[requested] ?? (choices.length === 1 ? choices[0] : undefined);
  const batches = seed?.batches ?? [];
  const batch =
    batches.find((b) => String(b.batch_number) === params.get("batch")) ??
    (batches.length === 1 ? batches[0] : undefined);
  const page = Math.max(
    1,
    Math.min(100000000, Number.parseInt(params.get("page") || "1", 10) || 1),
  );
  const list = useQuery({
    queryKey: ["trace-members", scope, batch?.batch_number, page],
    enabled: enabled && Boolean(batch),
    queryFn: ({ signal }) =>
      loadMembers(scope!, batch!.batch_number, page, signal),
  });
  const selected = list.data?.items.find(
    (r) => rowKey(r) === params.get("item"),
  );
  const update = (fields: Record<string, string>, clear: string[] = []) => {
    const next = new URLSearchParams(params);
    clear.forEach((k) => next.delete(k));
    Object.entries(fields).forEach(([k, v]) => next.set(k, v));
    setParams(next);
  };
  const comparison = useRef<HTMLDivElement>(null);
  const selectedKey = selected && rowKey(selected);
  useEffect(() => {
    if (selectedKey) comparison.current?.scrollIntoView?.({ block: "start" });
  }, [selectedKey]);
  const canReadTags = caps.has("annotation_task.read");
  const channel = { oss: "OSS 导入", web: "网页上传", historical: "历史上传" };
  return (
    <StandardPageScaffold
      header={{
        title: "数据回溯",
        breadcrumbs: [
          { key: "production", label: "数据生产" },
          { key: "trace", label: "数据回溯" },
        ],
      }}
      state={
        <div className={styles.workspace}>
          {!scope?.projectId || !scope.regionCode ? (
            <Alert type="info" title="请选择项目和区域以回溯数据" />
          ) : (
            <>
              <Card>
                <Search
                  key={JSON.stringify([scope, id])}
                  scope={scope}
                  initial={id}
                  submit={(value) => setParams({ id: value })}
                />
                {origins.isFetching && <p role="status">正在查找上传记录…</p>}
                {origins.error && (
                  <Alert
                    type="error"
                    title="上传记录读取失败"
                    description={origins.error.message}
                  />
                )}
                {origins.data?.length === 0 &&
                  (related.isPending ? (
                    <p role="status">正在检索匹配项…</p>
                  ) : related.error ? (
                    <Alert
                      type="error"
                      title="文件名检索失败"
                      description={related.error.message}
                    />
                  ) : related.data?.length ? (
                    <div className={styles.matches}>
                      <p>
                        选择匹配的文件名或 ID 以查看上传批次（最多显示 20 项）
                      </p>
                      {related.data.map((value) => (
                        <Button
                          key={value}
                          type="link"
                          onClick={() => setParams({ id: value })}
                        >
                          {value}
                        </Button>
                      ))}
                    </div>
                  ) : (
                    <Empty description="当前范围内未找到匹配的文件名或 ID" />
                  ))}
                {choices.length > 1 && (
                  <label className={styles.field}>
                    选择问题样本
                    <Select
                      placeholder="匹配多个样本，请选择数据版本"
                      value={seed ? originKey(seed) : undefined}
                      onChange={(v) =>
                        update({ sample: v }, ["batch", "page", "item"])
                      }
                      options={choices.map((s) => ({
                        value: originKey(s),
                        label: s.data
                          ? s.data.identity.dataset_id +
                            " · " +
                            s.data.identity.episode_id +
                            " · v" +
                            s.data.version
                          : s.identifier,
                      }))}
                    />
                  </label>
                )}
                {seed &&
                  (batches.length ? (
                    <label className={styles.field}>
                      上传批次
                      <Select
                        aria-label="上传批次"
                        value={batch?.batch_number}
                        placeholder="样本有多次上传记录，请选择批次"
                        onChange={(v) =>
                          update({ batch: String(v) }, ["page", "item"])
                        }
                        options={batches.map((b) => ({
                          value: b.batch_number,
                          label: `第 ${b.batch_number} 次上传 · ${channel[b.channel]} · ${new Date(b.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`,
                        }))}
                      />
                    </label>
                  ) : (
                    <Alert type="info" title="该版本没有可追溯的上传批次记录" />
                  ))}
              </Card>
              {seed && (
                <div ref={comparison} className={styles.comparison}>
                  {seed.data ? (
                    <SampleCard
                      key={seed.identifier}
                      title="问题样本"
                      scope={scope}
                      sample={seed}
                      canReadTags={canReadTags}
                    />
                  ) : (
                    <Card size="small" title="问题上传">
                      <span className={styles.identifier}>
                        {seed.identifier}
                      </span>
                    </Card>
                  )}
                  {selected?.data &&
                    selected.data.data_version_id !==
                      seed.data?.data_version_id && (
                      <SampleCard
                        key={rowKey(selected)}
                        title="正在核查的样本"
                        scope={scope}
                        sample={selected}
                        canReadTags={canReadTags}
                      />
                    )}
                  {selected && !selected.data && (
                    <RawFiles
                      key={rowKey(selected)}
                      scope={scope}
                      member={selected}
                    />
                  )}
                </div>
              )}
              {batch && (
                <Card
                  title="同批数据"
                  extra={
                    list.data &&
                    `${list.data.upload_count} 次上传登记 · ${list.data.total} 条记录`
                  }
                >
                  {batch.channel === "historical" && (
                    <p>历史批次只保留可确认的上传关系。</p>
                  )}
                  {list.error ? (
                    <Alert
                      type="error"
                      title="同批数据读取失败"
                      description={list.error.message}
                    />
                  ) : (
                    <Table<Member>
                      rowKey={rowKey}
                      loading={list.isFetching}
                      dataSource={list.data?.items ?? []}
                      pagination={false}
                      size="small"
                      scroll={{ x: 780 }}
                      columns={[
                        {
                          title: "样本 / 上传",
                          key: "sample",
                          render: (_, r) => (
                            <div className={styles.identifier}>
                              {r.data?.identity.episode_id || r.upload_id}
                              {r.data && (
                                <small>
                                  v{r.data.version} · {r.data.frame_count} 帧 ·{" "}
                                  {r.upload_id}
                                </small>
                              )}
                              {(seed?.data
                                ? r.data?.data_version_id === seed.identifier
                                : r.upload_id === seed?.identifier) && (
                                <Tag color="gold">问题来源</Tag>
                              )}
                            </div>
                          ),
                        },
                        {
                          title: "采集描述",
                          dataIndex: "collection_description",
                          render: (v) => v || "未记录",
                        },
                        {
                          title: "上传日备注",
                          dataIndex: "upload_note",
                          render: (v) => v || "未记录",
                        },
                        {
                          title: "核查",
                          key: "inspect",
                          render: (_, r) =>
                            r.data ? (
                              <Button
                                size="small"
                                disabled={
                                  r.data.data_version_id ===
                                    seed?.data?.data_version_id ||
                                  rowKey(r) === params.get("item")
                                }
                                onClick={() => update({ item: rowKey(r) })}
                              >
                                查看样本
                              </Button>
                            ) : r.raw_source_ids.length ? (
                              <Button
                                size="small"
                                onClick={() => update({ item: rowKey(r) })}
                              >
                                查看原始文件
                              </Button>
                            ) : (
                              <span>尚无可查看的数据</span>
                            ),
                        },
                      ]}
                    />
                  )}
                  {list.data && list.data.total > pageSize && (
                    <Pagination
                      current={page}
                      pageSize={pageSize}
                      total={list.data.total}
                      showSizeChanger={false}
                      onChange={(v) => update({ page: String(v) }, ["item"])}
                    />
                  )}
                </Card>
              )}
            </>
          )}
        </div>
      }
    />
  );
}
