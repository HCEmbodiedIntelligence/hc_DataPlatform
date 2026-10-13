import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Breadcrumb,
  Button,
  Empty,
  Spin,
  Table,
  Typography,
} from "antd";
import {
  ArrowUp,
  Download,
  File,
  FileText,
  Folder,
  FolderOpen,
  RefreshCw,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { useScopeQueryRevision } from "../../../shared/api/use-scoped-query";
import { formatBytes } from "../upload/upload-contract";
import {
  authorizeOriginalFile,
  getOriginalDirectory,
  getOriginalTextPreview,
  type OriginalDirectoryEntry,
} from "./original-source-api";
import styles from "./OriginalFileExplorer.module.css";

type Props = { scope: IngestScope; importId: string; canDownload?: boolean };

export default function OriginalFileExplorer(props: Props) {
  const revision = useScopeQueryRevision();
  return (
    <Explorer
      key={`${revision}:${props.scope.organizationId}:${props.scope.projectId}:${props.scope.regionCode}:${props.importId}`}
      {...props}
    />
  );
}

function Explorer({ scope, importId, canDownload = true }: Props) {
  const [path, setPath] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<OriginalDirectoryEntry | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current = new AbortController();
    return () => controller.current?.abort();
  }, []);
  const revision = useScopeQueryRevision();
  const identity = [
    "original-directory",
    revision,
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    importId,
  ];
  const query = useQuery({
    queryKey: [...identity, path, offset],
    queryFn: ({ signal }) =>
      getOriginalDirectory(scope, importId, path, offset, signal),
    retry: false,
    staleTime: 60_000,
  });
  const preview = useQuery({
    queryKey: [...identity, "text", selected?.path],
    queryFn: ({ signal }) =>
      getOriginalTextPreview(scope, importId, selected!.path, signal),
    enabled: Boolean(selected?.text_preview),
    retry: false,
    gcTime: 0,
  });
  function navigate(next: string) {
    setPath(next);
    setOffset(0);
    setSelected(null);
    setDownloadError(null);
  }
  async function download(file: OriginalDirectoryEntry) {
    if (downloading || !canDownload) return;
    const signal = controller.current!.signal;
    setDownloading(file.path);
    setDownloadError(null);
    try {
      const grant = await authorizeOriginalFile(
        scope,
        importId,
        file.path,
        true,
        signal,
      );
      signal.throwIfAborted();
      const link = document.createElement("a");
      link.href = grant.url;
      link.download = file.name;
      link.rel = "noopener noreferrer";
      document.body.append(link);
      link.click();
      link.remove();
    } catch (error) {
      if (!signal.aborted)
        setDownloadError(
          error instanceof Error ? error.message : "下载授权失败",
        );
    } finally {
      if (!signal.aborted) setDownloading(null);
    }
  }
  const parts = path ? path.split("/") : [];
  const page = query.data;
  return (
    <section className={styles.explorer} aria-label="原始文件浏览器">
      <header className={styles.header}>
        <div className={styles.heading}>
          <FolderOpen size={22} />
          <div>
            <strong>原始文件</strong>
            <p>按上传时的目录浏览，点击文件查看详情与下载</p>
          </div>
        </div>
        <Button
          aria-label="刷新文件夹"
          icon={<RefreshCw size={15} />}
          loading={query.isFetching}
          onClick={() => void query.refetch()}
        >
          刷新
        </Button>
      </header>
      <div className={styles.navigation}>
        <Button
          aria-label="返回上级文件夹"
          title="返回上级文件夹"
          disabled={!path}
          icon={<ArrowUp size={16} />}
          onClick={() => navigate(parts.slice(0, -1).join("/"))}
        />
        <Breadcrumb
          items={[
            {
              title: (
                <button className={styles.crumb} onClick={() => navigate("")}>
                  根目录
                </button>
              ),
            },
            ...parts.map((name, index) => ({
              title: (
                <button
                  className={styles.crumb}
                  onClick={() => navigate(parts.slice(0, index + 1).join("/"))}
                >
                  {name}
                </button>
              ),
            })),
          ]}
        />
      </div>
      {page && !query.isError && (
        <div className={styles.location}>
          <span>{page.local_path ? "本机查看路径" : "存储目录"}</span>
          <Typography.Text
            copyable={{ text: page.local_path ?? page.storage_prefix }}
            className={styles.path}
          >
            {page.local_path ?? page.storage_prefix}
          </Typography.Text>
          <small>
            {page.local_path
              ? "可复制到服务器的文件管理器打开"
              : "逻辑目录保留原文件名；每个文件的实际位置可在详情中查看"}
          </small>
        </div>
      )}
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          title="文件夹读取失败"
          description={query.error.message}
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      ) : (
        <>
          <Table<OriginalDirectoryEntry>
            className={styles.table}
            size="small"
            rowKey="path"
            pagination={false}
            loading={query.isPending}
            dataSource={page?.entries ?? []}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="此目录没有文件"
                />
              ),
            }}
            rowClassName={(file) =>
              file.path === selected?.path ? (styles.selected ?? "") : ""
            }
            columns={[
              {
                title: "文件名",
                key: "name",
                render: (_, file) => (
                  <button
                    className={styles.fileName}
                    onClick={() =>
                      file.kind === "DIRECTORY"
                        ? navigate(file.path)
                        : setSelected(file)
                    }
                  >
                    {file.kind === "DIRECTORY" ? (
                      <Folder className={styles.folderIcon} size={20} />
                    ) : file.text_preview ? (
                      <FileText size={19} />
                    ) : (
                      <File size={19} />
                    )}
                    <span>{file.name}</span>
                  </button>
                ),
              },
              {
                title: "大小",
                key: "size",
                width: 105,
                render: (_, file) => (
                  <div className={styles.size}>
                    {formatBytes(file.size)}
                    {file.kind === "DIRECTORY" && (
                      <small>{file.file_count} 个文件</small>
                    )}
                  </div>
                ),
              },
              {
                title: "操作",
                key: "actions",
                width: 76,
                render: (_, file) =>
                  file.kind === "FILE" ? (
                    <Button
                      type="text"
                      title="下载原文件"
                      aria-label={`下载 ${file.name}`}
                      disabled={!canDownload || Boolean(downloading)}
                      loading={downloading === file.path}
                      icon={<Download size={16} />}
                      onClick={() => void download(file)}
                    />
                  ) : null,
              },
            ]}
          />
          <footer className={styles.footer}>
            <span>
              {page?.file_count ?? 0} 个文件 ·{" "}
              {formatBytes(page?.total_bytes ?? 0)}
              <small>包含子文件夹</small>
            </span>
            {(page?.total ?? 0) > 50 && (
              <div className={styles.pager}>
                <Button
                  size="small"
                  disabled={offset === 0 || query.isFetching}
                  onClick={() => setOffset(offset - 50)}
                >
                  上一页
                </Button>
                <span>
                  {Math.floor(offset / 50) + 1} /{" "}
                  {Math.ceil((page?.total ?? 0) / 50)}
                </span>
                <Button
                  size="small"
                  disabled={
                    offset + 50 >= (page?.total ?? 0) || query.isFetching
                  }
                  onClick={() => setOffset(offset + 50)}
                >
                  下一页
                </Button>
              </div>
            )}
          </footer>
        </>
      )}
      {downloadError && <Alert type="error" title={downloadError} showIcon />}
      {selected && (
        <section className={styles.preview} aria-label="文件详情">
          <div className={styles.previewHeader}>
            <strong>{selected.name}</strong>
            <Button
              type="text"
              aria-label="关闭文件详情"
              icon={<X size={16} />}
              onClick={() => setSelected(null)}
            />
          </div>
          <div className={styles.fileFacts}>
            <span>原始路径</span>
            <Typography.Text copyable>{selected.path}</Typography.Text>
            <span>存储位置</span>
            <Typography.Text copyable>
              {selected.local_path ?? selected.storage_key}
            </Typography.Text>
            <span>文件大小</span>
            <span>
              {formatBytes(selected.size)} · {selected.size.toLocaleString()}{" "}
              字节
            </span>
          </div>
          <details className={styles.digest}>
            <summary>文件校验值 · SHA-256</summary>
            <Typography.Text copyable>{selected.sha256}</Typography.Text>
          </details>
          {selected.text_preview ? (
            <>
              {preview.isPending && (
                <div className={styles.loading}>
                  <Spin size="small" />
                  正在读取文件内容…
                </div>
              )}
              {preview.isError && (
                <Alert
                  type="warning"
                  title="文本预览不可用"
                  description={preview.error.message}
                />
              )}
              {preview.data && !preview.isError && (
                <>
                  {preview.data.truncated && (
                    <Alert
                      type="info"
                      title="仅预览前 256 KB，完整内容请下载原文件"
                    />
                  )}
                  <pre className={styles.textPreview}>
                    {preview.data.text || "（空文件）"}
                  </pre>
                </>
              )}
            </>
          ) : (
            <p className={styles.previewHint}>
              此文件请下载后使用相应工具打开。RGB 视频也可在原始数据回放中查看。
            </p>
          )}
          <Button
            type="primary"
            icon={<Download size={16} />}
            disabled={!canDownload || Boolean(downloading)}
            loading={downloading === selected.path}
            onClick={() => void download(selected)}
          >
            下载原文件
          </Button>
        </section>
      )}
    </section>
  );
}
