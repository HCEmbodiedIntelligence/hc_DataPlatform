import { useMemo, useState } from "react";
import { Button, Checkbox, Input } from "antd";
import { File, Search } from "lucide-react";
import { formatBytes } from "../../../features/ingest/upload/upload-contract";
import styles from "./oss-recordings.module.css";

type Props = {
  files: { path: string; size: number }[];
  selected: ReadonlySet<string>;
  disabled: boolean;
  onChange: (selection: Set<string>) => void;
};

export function ArchiveRecordingPicker({
  files,
  selected,
  disabled,
  onChange,
}: Props) {
  const [query, setQuery] = useState("");
  const recordings = useMemo(
    () => files.filter((file) => file.path.toLowerCase().endsWith(".mcap")),
    [files],
  );
  const filter = query.trim().toLowerCase();
  const visible = useMemo(
    () => recordings.filter((file) => file.path.toLowerCase().includes(filter)),
    [recordings, filter],
  );
  const checkedCount = visible.reduce(
    (count, file) => count + Number(selected.has(file.path)),
    0,
  );
  const selectedBytes = recordings.reduce(
    (bytes, file) => bytes + (selected.has(file.path) ? file.size : 0),
    0,
  );

  function toggle(paths: string[], checked: boolean) {
    const next = new Set(selected);
    for (const path of paths) {
      if (checked) next.add(path);
      else next.delete(path);
    }
    onChange(next);
  }

  return (
    <section className={styles.picker} aria-label="选择录制">
      <div className={styles.top}>
        <div className={styles.heading}>
          <h3>选择录制</h3>
          <span aria-live="polite">
            已选 <strong>{selected.size}</strong> / {recordings.length} 条 ·{" "}
            {formatBytes(selectedBytes)}
          </span>
        </div>
        <Input
          allowClear
          prefix={<Search size={16} aria-hidden />}
          aria-label="搜索录制"
          placeholder="搜索文件名或目录"
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className={styles.toolbar}>
        <Checkbox
          disabled={disabled || !visible.length}
          checked={visible.length > 0 && checkedCount === visible.length}
          indeterminate={checkedCount > 0 && checkedCount < visible.length}
          onChange={(event) =>
            toggle(
              visible.map((file) => file.path),
              event.target.checked,
            )
          }
        >
          {filter ? `全选匹配项（${visible.length}）` : "全选"}
        </Checkbox>
        <Button
          type="link"
          size="small"
          disabled={disabled || !selected.size}
          onClick={() => onChange(new Set())}
        >
          清空选择
        </Button>
      </div>
      <div className={styles.list} role="group" aria-label="录制文件">
        {visible.map((file) => {
          const split = file.path.lastIndexOf("/") + 1;
          return (
            <label
              key={file.path}
              className={styles.row}
              data-selected={selected.has(file.path)}
              title={file.path}
            >
              <input
                type="checkbox"
                aria-label={file.path}
                checked={selected.has(file.path)}
                disabled={disabled}
                onChange={(event) => toggle([file.path], event.target.checked)}
              />
              <span className={styles.fileIcon}>
                <File size={16} aria-hidden />
              </span>
              <span className={styles.name}>
                <strong>{file.path.slice(split)}</strong>
                {split > 0 && <small>{file.path.slice(0, split)}</small>}
              </span>
              <span className={styles.size}>{formatBytes(file.size)}</span>
            </label>
          );
        })}
        {!visible.length && <p className={styles.empty}>没有匹配的录制</p>}
      </div>
    </section>
  );
}
