import {
  Alert,
  Button,
  Drawer,
  Input,
  Pagination,
  Select,
  Table,
  Tag,
} from "antd";
import { Cloud, FolderOpen, HardDrive, RefreshCw, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  useTaskFiles,
  useTaskUsage,
  type TaskUsage,
  type TaskUsageRow,
  type UsageCategory,
  type StorageFile,
} from "../../features/storage-overview/task-usage-api";
import type { CapacityScope } from "../../features/storage-overview/capacity-api";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState } from "../../shared/ui";
import styles from "./task-storage.module.css";

const categories: {
  key: UsageCategory;
  label: string;
  color: string;
  description: string;
}[] = [
  {
    key: "RAW",
    label: "原始数据",
    color: "#4f6de5",
    description: "上传的原始录制与清单",
  },
  {
    key: "EPISODE",
    label: "入库数据",
    color: "#238672",
    description: "独立成片数据包，含保留版本",
  },
  {
    key: "PROCESSING",
    label: "预览及处理",
    color: "#bd8128",
    description: "解析、播放预览与处理产物",
  },
  {
    key: "ANNOTATION",
    label: "标注文件",
    color: "#9364c4",
    description: "标签和时间段记录，含历史修订",
  },
  {
    key: "EXPORT",
    label: "导出包",
    color: "#cb6481",
    description: "已生成的实际下载包",
  },
  {
    key: "OTHER",
    label: "其他文件",
    color: "#7a8495",
    description: "已登记的其他项目文件",
  },
];

export function storageBytes(value: string | number) {
  const bytes = BigInt(value);
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let divisor = 1n,
    index = 0;
  while (index < units.length - 1 && bytes >= divisor * 1024n) {
    divisor *= 1024n;
    index += 1;
  }
  const tenths = (bytes * 10n + divisor / 2n) / divisor;
  return `${Number(tenths) / 10} ${units[index]}`;
}

const bytesFor = (data: Pick<TaskUsageRow, "categories">, key: UsageCategory) =>
  data.categories.find((c) => c.category === key)?.bytes ?? "0";
const countFor = (data: Pick<TaskUsageRow, "categories">, key: UsageCategory) =>
  data.categories.find((c) => c.category === key)?.file_count ?? 0;
const percentage = (part: string, whole: string) =>
  BigInt(whole) === 0n
    ? 0
    : Number((BigInt(part) * 1000n) / BigInt(whole)) / 10;
const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });

export function StorageFlow({ data }: { data: Pick<TaskUsage, "categories"> }) {
  const node = (key: UsageCategory, x: number, y: number, subtitle: string) => {
    const info = categories.find((c) => c.key === key)!;
    return (
      <g key={key} transform={`translate(${x},${y})`}>
        <rect
          width="210"
          height="98"
          rx="12"
          fill="white"
          stroke={info.color}
          strokeOpacity=".35"
        />
        <rect x="14" y="17" width="4" height="17" rx="2" fill={info.color} />
        <text x="28" y="31" className={styles.flowTitle}>
          {info.label}
        </text>
        <text x="16" y="60" className={styles.flowValue}>
          {storageBytes(bytesFor(data, key))}
        </text>
        <text x="16" y="82" className={styles.flowSmall}>
          {countFor(data, key).toLocaleString()} 个文件 · {subtitle}
        </text>
      </g>
    );
  };
  return (
    <section className={styles.flowCard} aria-label="数据关系与实际占用">
      <div className={styles.sectionHeading}>
        <div>
          <h2>数据从哪里来，空间用在哪里</h2>
          <p>节点显示实际文件占用；箭头说明生成或引用关系。</p>
        </div>
        <Tag>真实文件统计</Tag>
      </div>
      <div className={styles.flowScroll}>
        <svg
          viewBox="0 0 1110 320"
          role="img"
          aria-label="原始数据经过解析和切片生成入库数据；标注作为独立记录关联成片，导出生成额外文件包。"
        >
          <defs>
            <marker
              id="storage-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0 0 L8 4 L0 8" fill="#a5aebe" />
            </marker>
          </defs>
          <g
            fill="none"
            stroke="#a5aebe"
            strokeWidth="1.6"
            markerEnd="url(#storage-arrow)"
          >
            <path d="M226 94 H290" />
            <path d="M500 94 H564" />
            <path d="M774 94 H874" />
            <path d="M669 143 V211" strokeDasharray="5 4" />
            <path d="M774 263 H916 Q979 263 979 201 V143" />
            <path d="M121 143 V219" strokeDasharray="5 4" />
          </g>
          <g className={styles.flowSmall} textAnchor="middle">
            <text x="258" y="76">
              解析
            </text>
            <text x="532" y="67">
              裁剪
            </text>
            <text x="532" y="82">
              对齐
            </text>
            <text x="824" y="76">
              读取数据打包
            </text>
            <text x="723" y="181">
              关联标签 / 时间段
            </text>
            <text x="869" y="285">
              按导出选择合并标注
            </text>
          </g>
          {node("RAW", 16, 45, "原文件保留")}
          {node("PROCESSING", 290, 45, "额外生成")}
          {node("EPISODE", 564, 45, "独立成片")}
          {node("EXPORT", 874, 45, "额外生成")}
          {node("ANNOTATION", 564, 214, "不复制整份视频")}
          <g transform="translate(16,220)">
            <rect
              width="355"
              height="68"
              rx="10"
              fill="#f4f6fa"
              stroke="#dce2ec"
              strokeDasharray="4 3"
            />
            <text x="16" y="27" className={styles.flowTitle}>
              切片范围与数据关系
            </text>
            <text x="16" y="49" className={styles.flowSmall}>
              数据库保存索引；仅划定范围不会复制视频
            </text>
          </g>
        </svg>
      </div>
      <p className={styles.note}>
        原始文件、成片、标注和导出分别计量。修改标注不会增加一整份成片；重新生成的成片版本、预览和导出文件会实际占空间。
      </p>
    </section>
  );
}

export function LocationOverview({ data }: { data: TaskUsage }) {
  const locations = data.storage_locations ?? [];
  const local = locations.find((item) => item.location === "LOCAL");
  const cloud = locations.find((item) => item.location === "OSS");
  const objects = local?.categories.filter((item) => item.category.startsWith("minio-")) ?? [];
  const shared = local?.categories.filter((item) => !item.category.startsWith("minio-")) ?? [];
  const keys = [...new Set([...objects.map((item) => item.category.slice(6)), ...(cloud?.categories.map((item) => item.category) ?? [])])];
  const purposeOrder = ["raw", "episodes", "collections", "derived-mcap", "derived-evidence", "recording-previews", "episode-previews", "episode-deliveries", "annotations", "other", "oss-smoke"];
  const paired = (key: string) => Number(objects.some((item) => item.category === `minio-${key}`) && cloud?.categories.some((item) => item.category === key));
  keys.sort((a, b) => paired(b) - paired(a) || purposeOrder.indexOf(a) - purposeOrder.indexOf(b));
  const retention = {
    KEEP: { text: "长期保留", color: "green" },
    CONDITIONAL: { text: "按条件回收", color: "gold" },
    REVIEW: { text: "待核对", color: "default" },
  };
  const size = (value: string | null | undefined, complete: boolean | undefined) =>
    value === null || (!complete && value === undefined) ? "未取得" : value === undefined ? "—" : storageBytes(value);
  return (
    <section className={styles.locationSection} aria-label="存储位置与数据用途">
      <div className={styles.sectionHeading}>
        <div><h2>存储分布</h2><p>同类数据横向对照 · 本地历史数据与 OSS 数据分别计量</p></div>
        <Tag>按用途对齐</Tag>
      </div>
      <div className={styles.storageBoard}>
        <div className={styles.storagePair}>
          {[local, cloud].map((location, index) => (
            <section key={index} className={`${styles.storageHero} ${index ? styles.cloudHero : styles.localHero}`} aria-label={`${location?.label ?? (index ? "阿里云 OSS" : "本地服务器")}数据分类`}>
              <div className={styles.heroHeading}><span className={styles.storageIcon}>{index ? <Cloud size={22} /> : <HardDrive size={22} />}</span><div><h3>{location?.label ?? (index ? "阿里云 OSS" : "本地服务器")}</h3><span>{index ? "当前目录内的对象" : "已统计的平台数据"}</span></div></div>
              <strong className={styles.storageHeroValue}>{location && (location.complete || location.categories.length) ? storageBytes(location.total_bytes) : "暂不可用"}</strong>
              <p className={styles.storageHeroScope}>{location?.scope ?? "当前版本未接入该位置统计"}</p>
              {location && !location.complete && <Alert type="warning" showIcon title="部分容量未取得" />}
            </section>
          ))}
        </div>
        <div role="table" aria-label="按用途对照本地和OSS" className={styles.pairedRows}>
          {keys.map((key) => {
            const here = objects.find((item) => item.category === `minio-${key}`);
            const there = cloud?.categories.find((item) => item.category === key);
            const item = there ?? here!;
            const label = item.label.replace(/^本地对象 · /, "");
            if (!here || !there) return <div role="row" key={key} className={styles.singleStorageRow}>
              <div role="cell" aria-colspan={2} className={styles.singleStorageCell}>
                <div className={styles.pairedValue}><div className={styles.singleStorageTitle}><h4>{label}</h4><Tag>{here ? "本地保存" : "OSS 保存"}</Tag></div><strong>{size(item.bytes, here ? local?.complete : cloud?.complete)}</strong></div>
                <div className={styles.pairedMeta}><Tag color={retention[item.retention].color}>{item.retention === "CONDITIONAL" ? <a href="/storage/lifecycle">按条件回收 · 配置</a> : retention[item.retention].text}</Tag></div>
                <p>{item.description}</p>
              </div>
            </div>;
            return <div role="row" key={key} className={styles.storagePair}>
              {[here, there].map((entry, index) => <div role="cell" key={index} className={styles.pairedCell} aria-label={`${label} · ${index ? "OSS" : "本地"}`}>
                <div className={styles.pairedValue}><h4>{label}</h4><strong className={!entry ? styles.mutedValue : undefined}>{size(entry?.bytes, index ? cloud?.complete : local?.complete)}</strong></div>
                <div className={styles.pairedMeta}>{entry ? <Tag color={retention[entry.retention].color}>{entry.retention === "CONDITIONAL" ? <a href="/storage/lifecycle">按条件回收 · 配置</a> : retention[entry.retention].text}</Tag> : <span>该位置未统计到此类数据</span>}</div>
                <p>{entry?.description ?? "有数据后会在这里显示对应容量。"}</p>
              </div>)}
            </div>;
          })}
        </div>
      </div>
      <p className={styles.note}>同一行表示用途相同，不表示文件完全相同或已完成同步；本地包含尚未上传 OSS 的历史数据。</p>
      {shared.length > 0 && <section className={styles.sharedSection} aria-label="本地公共资源与运行缓存">
        <div className={styles.sectionHeading}><div><h3>本地公共资源与运行缓存</h3><p>统一展示，已包含在本地合计中</p></div><Tag>仅本地计量</Tag></div>
        <div className={styles.sharedGrid}>{shared.map((item) => <article key={item.category} className={styles.sharedCard}>
          <div className={styles.sharedTitle}><h4>{item.label}</h4><Tag color={retention[item.retention].color}>{item.retention === "CONDITIONAL" ? <a href="/storage/lifecycle">按条件回收 · 配置</a> : retention[item.retention].text}</Tag></div>
          <strong>{size(item.bytes, local?.complete)}</strong><p>{item.description}</p>
        </article>)}</div>
      </section>}
      <details className={styles.capacityNotes}><summary>统计范围与保留说明</summary>
        {locations.map((item) => <p key={item.location}><b>{item.label}：</b>{item.note}</p>)}
        <p>“按条件回收”不等于整类数据现在就能删除，也不代表已计算可释放空间。这里只统计和说明，不执行清理。</p>
      </details>
    </section>
  );
}

function DiskOverview({ data }: { data: TaskUsage }) {
  const disk = data.disk;
  return (
    <div className={styles.topGrid}>
      <section className={styles.diskCard} aria-label="实际磁盘用量">
        <div className={styles.cardLabel}>
          <HardDrive size={18} />
          <h2>本地服务器磁盘</h2>
          <span>文件系统实测</span>
        </div>
        {disk ? (
          <>
            <div className={styles.diskNumbers}>
              <strong>{storageBytes(disk.available_bytes)}</strong>
              <span>可用 / 共 {storageBytes(disk.total_bytes)}</span>
              <b>{percentage(disk.used_bytes, disk.total_bytes)}% 已使用</b>
            </div>
            <div
              className={styles.diskBar}
              aria-label={`已使用 ${percentage(disk.used_bytes, disk.total_bytes)}%`}
            >
              <span
                style={{
                  width: `${percentage(disk.used_bytes, disk.total_bytes)}%`,
                }}
              />
              <i
                style={{
                  width: `${percentage(disk.reserved_bytes, disk.total_bytes)}%`,
                }}
              />
            </div>
            <div className={styles.diskLegend}>
              <span>
                已用 <b>{storageBytes(disk.used_bytes)}</b>
              </span>
              <span>
                系统预留 <b>{storageBytes(disk.reserved_bytes)}</b>
              </span>
              <span>
                可用 <b>{storageBytes(disk.available_bytes)}</b>
              </span>
            </div>
          </>
        ) : (
          <p>当前部署暂未提供磁盘总量与剩余空间。</p>
        )}
        <p className={styles.note}>
          整块磁盘还包含系统、容器、缓存及其他文件，不能用项目文件合计替代。
        </p>
      </section>
      <section className={styles.projectCard} aria-label="当前项目文件总量">
        <div className={styles.cardLabel}>
          <FolderOpen size={18} />
          <h2>当前项目文件</h2>
        </div>
        <strong>{storageBytes(data.total_bytes)}</strong>
        <p>
          {data.file_count.toLocaleString()} 个实际文件 ·{" "}
          {data.tasks.filter((t) => t.task_code).length} 个任务
        </p>
        <span className={styles.note}>
          按对象存储实际文件大小统计，共享文件只计一次。
        </span>
      </section>
    </div>
  );
}

export function TaskFilesPanel({
  scope,
  task,
  observedAt,
}: {
  scope: CapacityScope;
  task: TaskUsageRow;
  observedAt: string;
}) {
  const [category, setCategory] = useState<UsageCategory>();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search);
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const files = useTaskFiles(
    scope,
    task.group_id,
    category,
    query,
    offset,
    observedAt,
  );
  return (
    <div className={styles.details}>
      <div className={styles.detailSummary}>
        <strong>{storageBytes(task.total_bytes)}</strong>
        <span>{task.file_count.toLocaleString()} 个文件</span>
        <span>
          标注完成 {task.annotation_complete_count} / {task.episode_count} 条
        </span>
      </div>
      {task.shared_bytes !== "0" ? (
        <Alert
          type="info"
          title={`另有关联共享文件 ${storageBytes(task.shared_bytes)}，占用单独计入“跨任务共享文件”。`}
        />
      ) : null}
      <div
        className={styles.categoryFilters}
        role="group"
        aria-label="文件类型筛选"
      >
        <Button
          type={category === undefined ? "primary" : "default"}
          onClick={() => {
            setCategory(undefined);
            setOffset(0);
          }}
        >
          全部文件
        </Button>
        {categories.map((c) => (
          <Button
            key={c.key}
            type={category === c.key ? "primary" : "default"}
            onClick={() => {
              setCategory(c.key);
              setOffset(0);
            }}
          >
            {c.label} · {storageBytes(bytesFor(task, c.key))}
          </Button>
        ))}
      </div>
      <Input
        aria-label="搜索任务文件"
        prefix={<Search size={15} />}
        placeholder="搜索文件名、录制、机器人或状态"
        allowClear
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {files.isError ? (
        <PageState
          state="error"
          label="任务文件明细"
          onRetry={() => void files.refetch()}
        />
      ) : (
        <>
          <p className={styles.note}>
            当前筛选：{files.data?.total_count ?? 0} 个文件 ·{" "}
            {storageBytes(files.data?.total_bytes ?? "0")}
            。点击文件行可展开名称、相对路径与关联任务。
          </p>
          <Table<StorageFile>
            size="small"
            loading={files.isPending}
            rowKey="file_id"
            dataSource={files.data?.items ?? []}
            pagination={false}
            scroll={{ x: 760 }}
            expandable={{
              expandedRowRender: (file) => (
                <dl className={styles.fileInfo}>
                  <dt>文件名称</dt>
                  <dd>{file.name}</dd>
                  <dt>相对路径</dt>
                  <dd>{file.relative_path}</dd>
                  <dt>所属数据</dt>
                  <dd>{file.record_name}</dd>
                  <dt>机器人</dt>
                  <dd>{file.robot_name}</dd>
                  <dt>关联任务</dt>
                  <dd>{file.related_task_names.join("、") || "未关联任务"}</dd>
                  <dt>实际大小</dt>
                  <dd>
                    {BigInt(file.bytes).toLocaleString()} 字节
                    {file.shared ? "（共享文件，只计一次）" : ""}
                  </dd>
                </dl>
              ),
              expandRowByClick: true,
            }}
            columns={[
              {
                title: "文件 / 所属数据",
                key: "name",
                render: (_, f) => (
                  <div className={styles.fileName}>
                    <strong>{f.name}</strong>
                    <span>
                      {f.record_name} · {f.robot_name}
                    </span>
                  </div>
                ),
              },
              {
                title: "类型",
                width: 110,
                render: (_, f) => (
                  <Tag>
                    {categories.find((c) => c.key === f.category)?.label}
                  </Tag>
                ),
              },
              {
                title: "大小",
                width: 100,
                render: (_, f) => storageBytes(f.bytes),
              },
              { title: "状态", dataIndex: "status", width: 95 },
              {
                title: "文件更新时间",
                width: 165,
                render: (_, f) => date(f.updated_at),
              },
            ]}
          />
          <Pagination
            current={Math.floor(offset / 30) + 1}
            pageSize={30}
            total={files.data?.total_count ?? 0}
            showSizeChanger={false}
            onChange={(p) => setOffset((p - 1) * 30)}
          />
        </>
      )}
    </div>
  );
}

export function TaskStorageDashboard({
  data,
  scope,
}: {
  data: TaskUsage;
  scope: CapacityScope;
}) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("size");
  const [selected, setSelected] = useState<string>();
  const task = data.tasks.find((t) => t.group_id === selected);
  const tasks = useMemo(
    () =>
      data.tasks
        .filter((t) =>
          `${t.task_name} ${t.task_code ?? ""}`
            .toLowerCase()
            .includes(search.toLowerCase()),
        )
        .sort((a, b) =>
          sort === "code"
            ? (a.task_code ?? "~").localeCompare(b.task_code ?? "~")
            : Number(BigInt(b.total_bytes) - BigInt(a.total_bytes)),
        ),
    [data.tasks, search, sort],
  );
  return (
    <>
      {data.warnings?.map((w) => (
        <Alert key={w} type="warning" showIcon title={w} />
      ))}
      <div className={styles.metrics}>
        {categories
          .filter((c) => c.key !== "OTHER" || countFor(data, c.key) > 0)
          .map((c) => (
            <section key={c.key} style={{ borderTopColor: c.color }}>
              <span>{c.label}</span>
              <strong>{storageBytes(bytesFor(data, c.key))}</strong>
              <small>{c.description}</small>
            </section>
          ))}
      </div>
      <StorageFlow data={data} />
      <DiskOverview data={data} />
      <LocationOverview data={data} />
      <section className={styles.taskCard}>
        <div className={styles.sectionHeading}>
          <div>
            <h2>按任务查看占用</h2>
            <p>
              标注进度统计当前入库成片；标注文件大小只计算标签与时间段等记录。
            </p>
          </div>
          <span>{tasks.length} 项</span>
        </div>
        <div className={styles.toolbar}>
          <Input
            prefix={<Search size={16} />}
            aria-label="搜索容量任务"
            placeholder="搜索任务名称或任务号"
            allowClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select
            aria-label="任务排序"
            value={sort}
            onChange={setSort}
            options={[
              { value: "size", label: "占用从大到小" },
              { value: "code", label: "按任务号" },
            ]}
          />
        </div>
        <Table<TaskUsageRow>
          rowKey="group_id"
          size="middle"
          dataSource={tasks}
          pagination={
            tasks.length > 10 ? { pageSize: 10, showSizeChanger: false } : false
          }
          scroll={{ x: 1110 }}
          columns={[
            {
              title: "任务",
              key: "task",
              width: 225,
              fixed: "left",
              render: (_, t) => (
                <div className={styles.taskName}>
                  <strong>{t.task_name}</strong>
                  <span>
                    {t.task_code ? `任务 ${t.task_code} · ` : ""}
                    {t.raw_count} 份原始录制 · {t.episode_count} 条入库成片
                  </span>
                </div>
              ),
            },
            {
              title: "总占用",
              width: 110,
              render: (_, t) => <strong>{storageBytes(t.total_bytes)}</strong>,
            },
            ...categories
              .filter((c) => c.key !== "OTHER" || countFor(data, "OTHER") > 0)
              .map((c) => ({
                title: c.label,
                key: c.key,
                width: 105,
                render: (_: unknown, t: TaskUsageRow) =>
                  storageBytes(bytesFor(t, c.key)),
              })),
            {
              title: "标注进度",
              width: 120,
              render: (_, t) => (
                <div className={styles.annotation}>
                  <strong>
                    {t.annotation_complete_count} / {t.episode_count} 条
                  </strong>
                  <span>
                    {t.episode_count
                      ? `${t.episode_count - t.annotation_complete_count} 条待完成`
                      : "暂无入库成片"}
                  </span>
                </div>
              ),
            },
            {
              title: "明细",
              width: 100,
              fixed: "right",
              render: (_, t) => (
                <Button size="small" onClick={() => setSelected(t.group_id)}>
                  查看文件
                </Button>
              ),
            },
          ]}
        />
        <p className={styles.note}>
          文件内容按实际大小汇总，不代表磁盘块分配。多个任务共同引用的文件单独记入“跨任务共享文件”，不会重复累加。
        </p>
      </section>
      <Drawer
        open={Boolean(task)}
        onClose={() => setSelected(undefined)}
        title={
          task
            ? `${task.task_name}${task.task_code ? ` · ${task.task_code}` : ""}`
            : "任务文件"
        }
        size={1060}
        destroyOnHidden
      >
        {task ? (
          <TaskFilesPanel
            key={task.group_id}
            task={task}
            scope={scope}
            observedAt={data.observed_at}
          />
        ) : null}
      </Drawer>
    </>
  );
}

export function TaskStoragePage() {
  const shellScope = useShellStore((s) => s.scope);
  const scope = shellScope?.projectId ? (shellScope as CapacityScope) : null;
  const capabilities = useCapabilities();
  const [refresh, setRefresh] = useState(0);
  const usage = useTaskUsage(
    scope,
    capabilities.has("storage.overview.read"),
    refresh,
  );
  return (
    <div className={styles.page} data-page-id="P12">
      <header className={styles.header}>
        <div>
          <h1>存储容量</h1>
          <p>磁盘余量、任务文件占用与数据关系</p>
        </div>
        <div>
          <span>
            {usage.data ? `统计于 ${date(usage.data.observed_at)}` : ""}
          </span>
          <Button
            icon={<RefreshCw size={15} />}
            loading={usage.isFetching}
            disabled={!scope || !capabilities.has("storage.overview.read")}
            onClick={() => setRefresh((v) => v + 1)}
          >
            刷新用量
          </Button>
        </div>
      </header>
      {capabilities.loading ? (
        <PageState state="loading" label="存储容量" />
      ) : !capabilities.has("storage.overview.read") ? (
        <PageState state="forbidden" label="存储容量" />
      ) : !scope ? (
        <PageState state="empty" label="请选择项目" />
      ) : usage.isError ? (
        <PageState
          state="error"
          label="存储容量"
          onRetry={() => void usage.refetch()}
        />
      ) : usage.data ? (
        <TaskStorageDashboard data={usage.data} scope={scope} />
      ) : (
        <PageState state="loading" label="正在核对实际存储用量" />
      )}
    </div>
  );
}
