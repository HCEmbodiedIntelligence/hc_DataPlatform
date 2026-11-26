import { Tooltip } from "antd";
import type {
  DashboardAnalytics,
  DashboardWindow,
} from "../../../features/dashboard/api/client";
import { qualityFindingText } from "../../../shared/lib/quality-presentation";
import styles from "./AnalyticsPanel.module.css";

const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1,
});
const palette = [
  "#5966d9",
  "#15998c",
  "#c17a22",
  "#9371b7",
  "#4085b5",
  "#b85070",
];
const outcomeColors: Record<string, string> = {
  PASS: "#198567",
  REJECT: "#cf5651",
  RISK: "#bd801b",
};
const outcomeNames: Record<string, string> = {
  PASS: "通过",
  REJECT: "失败",
  RISK: "风险",
};

function QualityResults({ data }: { data?: DashboardAnalytics }) {
  const outcomes = Object.entries(data?.outcomes ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = outcomes.reduce((sum, [, n]) => sum + n, 0);
  const reports = data?.reports ?? {};
  let running = 0;
  const entries = total
    ? outcomes
    : Object.keys(outcomeNames).map((key): [string, number] => [key, 0]);
  const arcs = outcomes.map(([key, n]) => {
    const from = running;
    running += n;
    return `${outcomeColors[key]} ${(100 * from) / total}% ${(100 * running) / total}%`;
  });
  return (
    <section className={styles.card}>
      <h2>质检结果占比</h2>
      <p>每个数据包取所选时段内有效报告的最严重结果。已替代录制不计入。</p>

      <div className={styles.breakdown}>
        <div
          className={styles.ring}
          aria-hidden="true"
          style={
            total
              ? { background: `conic-gradient(${arcs.join(",")})` }
              : undefined
          }
        >
          <div>
            <strong>{number.format(total)}</strong>
            <small>个数据包</small>
          </div>
        </div>
        <ul>
          {entries.map(([key, n]) => (
            <li key={key}>
              <i style={{ background: outcomeColors[key] }} />
              <span>{outcomeNames[key] ?? key}</span>
              <strong>
                {number.format(n)}
                {total ? ` · ${percent.format(n / total)}` : " 个"}
              </strong>
            </li>
          ))}
        </ul>
      </div>
      <p>
        录制报告 {number.format(reports.recording ?? 0)} 份 · Episode 报告{" "}
        {number.format(reports.episode ?? 0)} 份
        {reports.rollout
          ? ` · 数据包报告 ${number.format(reports.rollout)} 份`
          : ""}
      </p>
    </section>
  );
}

function QualityIssues({ data }: { data?: DashboardAnalytics }) {
  const affected = data?.affected_packages ?? 0;
  const entries = Object.entries(data?.issues ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <section className={styles.card}>
      <h2>质检问题分类占比</h2>
      <p>占有问题数据包的比例。同一包可有多类问题，每类只计一次。</p>
      <strong>有问题的数据包 {number.format(affected)} 个</strong>
      {affected ? (
        <ul className={styles.issueList}>
          {entries.map(([code, n], index) => {
            const title = qualityFindingText(code).title;
            return (
              <li key={code}>
                <span title={code}>
                  {title && title !== "数据质量异常" ? title : code}
                </span>
                <strong>
                  {number.format(n)} 个 · {percent.format(n / affected)}
                </strong>
                <div aria-hidden="true">
                  <i
                    style={{
                      width: `${(100 * n) / affected}%`,
                      background: palette[index % palette.length],
                    }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div
          className={styles.emptyIssuePlot}
          role="img"
          aria-label="质检问题分类占比图，0 类问题"
        >
          <div className={styles.emptyIssueGrid}>
            <small>0 类问题</small>
          </div>
          <div className={styles.percentAxis} aria-hidden="true">
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </div>
        </div>
      )}
    </section>
  );
}

export function AnalyticsPanel({
  data,
  window,
}: {
  data?: DashboardAnalytics;
  window: DashboardWindow;
}) {
  const startMs = Date.parse(window.from);
  const rangeMs = Date.parse(window.to) - startMs;
  const hourly = rangeMs <= 86_400_000;
  const stride = hourly ? 3_600_000 : 86_400_000;
  const buckets = data?.buckets.length
    ? data.buckets
    : Array.from({ length: Math.ceil(rangeMs / stride) }, (_, i) => ({
        start: new Date(startMs + i * stride).toISOString(),
        episodes: 0,
        frames: 0,
        seconds: 0,
      }));
  const episodes = buckets.reduce((sum, b) => sum + b.episodes, 0);
  const frames = buckets.reduce((sum, b) => sum + b.frames, 0);
  const seconds = buckets.reduce((sum, b) => sum + b.seconds, 0);
  const label = (start: string) =>
    new Date(start).toLocaleString(
      "zh-CN",
      hourly
        ? { hour: "2-digit", minute: "2-digit", hour12: false }
        : { month: "numeric", day: "numeric" },
    );
  const peak = Math.max(0, ...buckets.map((b) => b.episodes));
  const step = Math.max(1, Math.ceil(peak / 3));
  const intervals = Math.max(1, Math.ceil(peak / step));
  const maximum = intervals * step;
  return (
    <div className={styles.grid}>
      <section className={`${styles.card} ${styles.volume}`}>
        <div className={styles.header}>
          <div>
            <h2>入库 Episode 趋势</h2>
            <p>
              已入库 Episode · 按采集开始时间{hourly ? "每小时" : "每 24 小时"}
              汇总
            </p>
          </div>
        </div>
        <div className={styles.totals}>
          <div>
            <small>入库条数</small>
            <strong>
              {number.format(episodes)} <small>条</small>
            </strong>
          </div>
          <div>
            <small>总帧数</small>
            <strong>
              {number.format(frames)} <small>帧</small>
            </strong>
          </div>
          <div>
            <small>总时长</small>
            <strong>
              {number.format(seconds < 60 ? seconds : seconds / 60)}{" "}
              <small>{seconds < 60 ? "秒" : "分钟"}</small>
            </strong>
          </div>
        </div>
        <div
          className={styles.plot}
          role="group"
          aria-label="入库 Episode 条数趋势"
        >
          <div className={styles.axis}>
            {Array.from({ length: intervals + 1 }, (_, i) => (
              <span key={i}>{number.format(maximum - i * step)} 条</span>
            ))}
          </div>
          <div
            className={styles.bars}
            data-zero={peak === 0 || undefined}
            style={{ backgroundSize: `100% ${100 / intervals}%` }}
          >
            {buckets.map((b, i) => {
              const end = buckets[i + 1]?.start ?? window.to;
              const tip = `${new Date(b.start).toLocaleString("zh-CN")} 至 ${new Date(end).toLocaleString("zh-CN")} · ${number.format(b.episodes)} 条 · ${number.format(b.frames)} 帧 · ${number.format(b.seconds)} 秒`;
              return (
                <Tooltip key={b.start} title={tip} trigger={["hover", "focus"]}>
                  <div className={styles.column} tabIndex={0} aria-label={tip}>
                    <i
                      style={{
                        height: `${(100 * b.episodes) / maximum}%`,
                      }}
                    />
                    {(i === 0 ||
                      i === buckets.length - 1 ||
                      (i % Math.ceil(buckets.length / 5) === 0 &&
                        i < buckets.length - 2)) && (
                      <small>{label(b.start)}</small>
                    )}
                  </div>
                </Tooltip>
              );
            })}
          </div>
        </div>
      </section>
      <QualityResults data={data} />
      <QualityIssues data={data} />
    </div>
  );
}
