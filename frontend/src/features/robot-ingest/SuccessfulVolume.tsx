import { Button } from "antd";
import { HardDriveUpload } from "lucide-react";
import type { IngestScope } from "../../entities/data-source";
import { formatStorageSize } from "../../shared/lib/metric-presentation";
import { useSuccessfulVolume } from "./onboarding";
import styles from "../ingest/upload/upload.module.css";

export function SuccessfulVolume({
  scope,
  robotId,
}: {
  scope: IngestScope | null;
  robotId?: string | null;
}) {
  const volume = useSuccessfulVolume(scope, robotId);
  return (
    <section aria-label="今日成功上传量" className={styles.metricTile}>
      <span className={styles.metricIcon}>
        <HardDriveUpload size={26} aria-hidden="true" />
      </span>
      <div className={styles.metricCopy}>
        <span className={styles.metricLabel}>今日成功上传量</span>
        <strong>
          {volume.isError
            ? "暂不可用"
            : volume.data
              ? formatStorageSize(volume.data.raw_bytes)
              : "加载中"}
        </strong>
        <span>
          {volume.data?.day ?? "当日"} · Asia/Shanghai · 当前项目 / Region
          {robotId ? " / 此机器人" : ""} ·
          按成功提交时间统计原始文件字节，上传重试去重
        </span>
        {volume.isError && (
          <Button type="link" onClick={() => void volume.refetch()}>
            重新加载统计
          </Button>
        )}
      </div>
    </section>
  );
}
