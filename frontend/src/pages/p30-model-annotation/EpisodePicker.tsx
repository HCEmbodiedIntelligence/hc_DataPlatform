import { Button, Form, Input, Select, Table, Tag, Tooltip } from "antd";
import { Layers3, Search } from "lucide-react";
import { useState } from "react";
import type { components } from "../../shared/api/generated/platform";
import { withinTimeRange, type TimeRange } from "../../shared/lib/time-range";
import { TimeRangeFilter } from "../../shared/ui/TimeRangeFilter";
import styles from "./styles.module.css";

export type Episode = components["schemas"]["ModelEpisode"];
const statuses = ["全部", "未标注", "已有标注", "待修改"] as const;
const time = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function EpisodePicker({
  items,
  selected,
  setSelected,
  blocked,
  busy,
  loading,
}: {
  items: Episode[];
  selected: string[];
  setSelected: (ids: string[]) => void;
  blocked: (item: Episode) => string | undefined;
  busy: boolean;
  loading: boolean;
}) {
  const [collection, setCollection] = useState<string>();
  const [batches, setBatches] = useState<number[]>([]);
  const [range, setRange] = useState<TimeRange>();
  const [status, setStatus] = useState<(typeof statuses)[number]>("全部");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const groups = new Map<string, string>();
  for (const item of items) {
    if (item.collection_task_id)
      groups.set(
        item.collection_task_id,
        "任务 " +
          (item.collection_task_code?.replace(/^0+(?=\d)/u, "") ??
            item.collection_task_id),
      );
  }
  const inCollection = items.filter(
    (item) => !collection || item.collection_task_id === collection,
  );
  const numbers = [
    ...new Set(
      inCollection.flatMap((item) => item.uploads.map((u) => u.batch_number)),
    ),
  ].sort((a, b) => b - a);
  const term = search.trim().toLowerCase();
  const matchUpload = (u: Episode["uploads"][number]) =>
    (!batches.length || batches.includes(u.batch_number)) &&
    withinTimeRange(u.uploaded_at, range);
  const visible = inCollection.filter(
    (item) =>
      (!term ||
        [item.label, item.episode_id].some((value) =>
          value.toLowerCase().includes(term),
        )) &&
      (!(batches.length || range) || item.uploads.some(matchUpload)) &&
      (status === "全部" ||
        (status === "未标注" && !item.tag_count) ||
        (status === "已有标注" && item.tag_count > 0) ||
        (status === "待修改" && item.status === "NEEDS_REVISION")),
  );
  const selectable = visible.filter((item) => !blocked(item));
  const keys = new Set(selected);
  const available = selectable.filter((item) => !keys.has(item.task_id));
  const filtered = !!(
    collection ||
    batches.length ||
    range ||
    term ||
    status !== "全部"
  );
  function reset() {
    setCollection(undefined);
    setBatches([]);
    setRange(undefined);
    setStatus("全部");
    setSearch("");
    setPage(1);
  }

  return (
    <section className={styles.picker} aria-labelledby="episode-heading">
      <div className={styles.sectionHead}>
        <h2 id="episode-heading">
          <Layers3 aria-hidden="true" size={20} />
          Episode 列表
        </h2>
        <Input
          aria-label="搜索 Episode"
          placeholder="搜索 Episode 名称或 ID"
          prefix={<Search size={15} aria-hidden="true" />}
          value={search}
          allowClear
          disabled={busy}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
        />
      </div>
      <Form layout="vertical" disabled={busy} className={styles.filters}>
        <Form.Item label="采集任务" htmlFor="model-collection">
          <Select
            id="model-collection"
            placeholder="全部采集任务"
            showSearch
            optionFilterProp="label"
            allowClear
            value={collection}
            options={Array.from(groups, ([value, label]) => ({ value, label }))}
            onChange={(value) => {
              setCollection(value);
              setBatches([]);
              setPage(1);
            }}
          />
        </Form.Item>
        <Form.Item label="上传批次" htmlFor="model-batch">
          <Select
            id="model-batch"
            placeholder="全部上传批次"
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            value={batches}
            maxTagCount={1}
            options={numbers.map((value) => ({
              value,
              label: `批次 ${value}`,
            }))}
            onChange={(value) => {
              setBatches(value);
              setPage(1);
            }}
          />
        </Form.Item>
        <Form.Item label="上传时间">
          <TimeRangeFilter
            label="上传时间"
            range={range}
            onChange={(value) => {
              setRange(value);
              setPage(1);
            }}
          />
        </Form.Item>
      </Form>
      <div className={styles.statusBar}>
        <div role="group" aria-label="标注状态" className={styles.tabs}>
          {statuses.map((value) => (
            <button
              type="button"
              key={value}
              aria-pressed={status === value}
              disabled={busy}
              onClick={() => {
                setStatus(value);
                setPage(1);
              }}
            >
              {value}
            </button>
          ))}
        </div>
        {filtered && (
          <Button type="link" size="small" onClick={reset} disabled={busy}>
            重置筛选
          </Button>
        )}
      </div>
      <div className={styles.listBar}>
        <span>
          匹配 <strong>{visible.length}</strong> 项
        </span>
        <Button
          type="link"
          size="small"
          disabled={busy || !available.length}
          onClick={() =>
            setSelected([...selected, ...available.map((item) => item.task_id)])
          }
        >
          选择全部匹配项
        </Button>
      </div>
      <Table<Episode>
        size="small"
        rowKey="task_id"
        dataSource={visible}
        loading={loading}
        locale={{
          emptyText: filtered
            ? "没有符合筛选条件的 Episode"
            : "当前项目暂无待标注 Episode",
        }}
        scroll={{ x: 560 }}
        pagination={{
          current: Math.min(
            page,
            Math.max(1, Math.ceil(visible.length / pageSize)),
          ),
          pageSize,
          showSizeChanger: true,
          pageSizeOptions: [10, 25, 50],
          hideOnSinglePage: visible.length <= 10,
          onChange: (p, size) => {
            setPageSize(size);
            setPage(size === pageSize ? p : 1);
          },
        }}
        rowSelection={{
          selectedRowKeys: selected,
          preserveSelectedRowKeys: true,
          onChange: (ids) => setSelected(ids.map(String)),
          getCheckboxProps: (item) => ({
            disabled: busy || !!blocked(item),
            "aria-label": `选择 ${item.label}`,
          }),
          getTitleCheckboxProps: () => ({
            "aria-label": "选择本页可用 Episode",
          }),
        }}
        columns={[
          {
            title: "Episode",
            key: "episode",
            render: (_, item) => (
              <div className={styles.identity}>
                <strong>{item.label}</strong>
                {blocked(item) ? (
                  <small>{blocked(item)}</small>
                ) : (
                  !item.allowed_tags.length && (
                    <small>采集方案尚未配置可用标签</small>
                  )
                )}
              </div>
            ),
          },
          {
            title: "上传批次 / 时间",
            key: "upload",
            render: (_, item) => {
              const uploads = item.uploads.filter(matchUpload);
              const first = uploads[0];
              if (!first)
                return <span className={styles.muted}>未关联上传记录</span>;
              return (
                <div className={styles.upload}>
                  <span>
                    批次 {first.batch_number}{" "}
                    {uploads.length > 1 && (
                      <Tooltip
                        title={uploads
                          .map(
                            (u) =>
                              `批次 ${u.batch_number} · ${time.format(new Date(u.uploaded_at))}`,
                          )
                          .join(" / ")}
                      >
                        <span tabIndex={0}>+{uploads.length - 1}</span>
                      </Tooltip>
                    )}
                  </span>
                  <small>{time.format(new Date(first.uploaded_at))}</small>
                </div>
              );
            },
          },
          {
            title: "标注状态",
            key: "status",
            render: (_, item) => (
              <div className={styles.state}>
                <Tag color={item.tag_count ? "blue" : undefined}>
                  {item.tag_count ? `已标注 · ${item.tag_count} 段` : "未标注"}
                </Tag>
                {item.status === "NEEDS_REVISION" && (
                  <Tag color="orange">待修改</Tag>
                )}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
}
