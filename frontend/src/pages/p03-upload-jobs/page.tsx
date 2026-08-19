import { useQuery } from "@tanstack/react-query";
import { Alert, Button } from "antd";
import { ArrowUp, FileJson2, ShieldCheck, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { dataUploadRoutes } from "../../app/shell/navigation-routes";
import { useIngestScope } from "../../features/ingest/use-ingest-scope";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { PageState, UiPageHeader } from "../../shared/ui";
import { ManifestPreflightPanel } from "./components/ManifestPreflightPanel";
import {
  UploadMethodPanel,
  type UploadSourceChoice,
} from "./components/UploadMethodPanel";
import { UploadQueuePanel } from "./components/UploadQueuePanel";
import { UploadRecordsPanel } from "./components/UploadRecordsPanel";
import {
  listFormalUploadSessions,
  preflightUploadManifest,
  type ManifestPreflight,
  type UploadStatus,
} from "./formal-client";
import styles from "./styles.module.css";
import {
  findManifestFile,
  findRawPackageFile,
  parseManifestFile,
  uploadProblemCopy,
  validateObjectStorageUri,
  type UploadProblemCopy,
} from "./upload-contract";
import { useUploadQueueStore } from "./upload-queue-store";

function useNetworkStatus(): boolean {
  const [online, setOnline] = useState(
    () => typeof navigator === "undefined" || navigator.onLine,
  );
  useEffect(() => {
    const markOnline = () => setOnline(true);
    const markOffline = () => setOnline(false);
    window.addEventListener("online", markOnline);
    window.addEventListener("offline", markOffline);
    return () => {
      window.removeEventListener("online", markOnline);
      window.removeEventListener("offline", markOffline);
    };
  }, []);
  return online;
}

export default function UploadJobsPage() {
  const scopeSnapshot = useIngestScope();
  const scope = useMemo(
    () => (scopeSnapshot ? { ...scopeSnapshot } : null),
    [
      scopeSnapshot?.organizationId,
      scopeSnapshot?.projectId,
      scopeSnapshot?.regionCode,
    ],
  );
  const capabilities = useCapabilities();
  const location = useLocation();
  const navigate = useNavigate();
  const newUploadTabRef = useRef<HTMLAnchorElement>(null);
  const recordsTabRef = useRef<HTMLAnchorElement>(null);
  const activeTab =
    location.pathname === dataUploadRoutes.records ? "records" : "new";
  const online = useNetworkStatus();
  const [sourceType, setSourceType] =
    useState<UploadSourceChoice>("BROWSER_MULTIPART");
  const [files, setFiles] = useState<readonly File[]>([]);
  const [objectStorageUri, setObjectStorageUri] = useState("");
  const [preflight, setPreflight] = useState<ManifestPreflight | null>(null);
  const [preflightStatus, setPreflightStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [preflightProblem, setPreflightProblem] =
    useState<UploadProblemCopy | null>(null);
  const [preflightNonce, setPreflightNonce] = useState(0);
  const [packageFilter, setPackageFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<UploadStatus>();

  const queueItems = useUploadQueueStore((state) => state.items);
  const recovering = useUploadQueueStore((state) => state.recovering);
  const recoverQueue = useUploadQueueStore((state) => state.recover);
  const startUpload = useUploadQueueStore((state) => state.start);
  const pauseUpload = useUploadQueueStore((state) => state.pause);
  const resumeUpload = useUploadQueueStore((state) => state.resume);
  const retryFailedParts = useUploadQueueStore(
    (state) => state.retryFailedParts,
  );
  const reattachAndResume = useUploadQueueStore(
    (state) => state.reattachAndResume,
  );
  const cancelUpload = useUploadQueueStore((state) => state.cancel);
  const clearSettled = useUploadQueueStore((state) => state.clearSettled);
  const canRead = capabilities.has("upload.read");
  const canManage = capabilities.has("upload.manage");

  useEffect(() => {
    if (scope && canRead) void recoverQueue(scope);
  }, [canRead, recoverQueue, scope]);

  useEffect(() => {
    if (!scope || files.length === 0) {
      setPreflight(null);
      setPreflightProblem(null);
      setPreflightStatus("idle");
      return;
    }
    const controller = new AbortController();
    const manifestFile = findManifestFile(files);
    setPreflight(null);
    setPreflightProblem(null);
    setPreflightStatus("loading");
    void (async () => {
      try {
        if (!manifestFile) throw new Error("MANIFEST_FILE_MISSING");
        const manifest = await parseManifestFile(manifestFile);
        const result = await preflightUploadManifest(
          scope,
          manifest,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setPreflight(result);
        setPreflightStatus("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        const normalized =
          error instanceof Error && error.message === "MANIFEST_FILE_MISSING"
            ? uploadProblemCopy({})
            : uploadProblemCopy(error);
        setPreflightProblem(
          error instanceof Error && error.message === "MANIFEST_FILE_MISSING"
            ? {
                title: "未发现 Manifest",
                detail:
                  "所选数据包中没有可识别的 .json Manifest；请选择 Manifest 与 RAW_MCAP。",
                requestId: null,
                retryable: false,
                status: 422,
                problemCode: "MANIFEST_FILE_MISSING",
              }
            : normalized,
        );
        setPreflightStatus("error");
      }
    })();
    return () => controller.abort();
  }, [files, preflightNonce, scope]);

  const records = useQuery({
    queryKey: [
      "p03-formal-upload-sessions",
      scope?.projectId,
      scope?.regionCode,
      packageFilter.trim(),
      statusFilter,
    ],
    enabled: activeTab === "records" && Boolean(scope) && canRead,
    staleTime: 10_000,
    queryFn: ({ signal }) => {
      if (!scope) throw new Error("INGEST_SCOPE_UNAVAILABLE");
      return listFormalUploadSessions(
        scope,
        {
          limit: 100,
          status: statusFilter,
          data_package_id: packageFilter.trim() || undefined,
        },
        signal,
      );
    },
  });

  const objectUriError =
    sourceType === "OBJECT_STORAGE_REFERENCE"
      ? validateObjectStorageUri(objectStorageUri)
      : null;
  const rawPackageFile = useMemo(
    () =>
      preflight && sourceType === "BROWSER_MULTIPART"
        ? findRawPackageFile(files, preflight.manifest)
        : null,
    [files, preflight, sourceType],
  );
  const localSizeMatches =
    !rawPackageFile ||
    !preflight ||
    rawPackageFile.size === preflight.manifest.file_size;
  const startBlockedReason = !online
    ? "网络未连接"
    : !canManage
      ? "当前授权只有查看权限"
      : preflightStatus !== "ready" || !preflight
        ? "请先通过 Manifest 预检"
        : sourceType === "BROWSER_MULTIPART" && !rawPackageFile
          ? "未找到 Manifest 声明的 RAW_MCAP"
          : !localSizeMatches
            ? "本地文件大小与 Manifest 不一致"
            : objectUriError;

  if (!scope) {
    return (
      <main className={styles.page}>
        <PageState
          state="feature-unavailable"
          label="数据上传"
          description="请先选择项目和区域。"
        />
      </main>
    );
  }
  if (capabilities.loading) {
    return (
      <main className={styles.page}>
        <PageState state="loading" label="数据上传" layout="workbench" />
      </main>
    );
  }
  if (!canRead) {
    return (
      <main className={styles.page}>
        <PageState state="forbidden" label="数据上传" />
      </main>
    );
  }

  const changeSourceType = (value: UploadSourceChoice) => {
    setSourceType(value);
    setFiles([]);
    setPreflight(null);
    setPreflightProblem(null);
    setPreflightStatus("idle");
  };

  const beginUpload = () => {
    if (!preflight || startBlockedReason) return;
    void startUpload({ scope, preflight, sourceType, files, objectStorageUri });
    setFiles([]);
    setPreflight(null);
    setPreflightProblem(null);
    setPreflightStatus("idle");
    if (sourceType === "OBJECT_STORAGE_REFERENCE") setObjectStorageUri("");
  };

  const handleTabKeyDown = (
    event: React.KeyboardEvent<HTMLAnchorElement>,
    target: string,
    targetRef: React.RefObject<HTMLAnchorElement | null>,
  ) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    void navigate(target);
    queueMicrotask(() => targetRef.current?.focus());
  };

  return (
    <main className={styles.page}>
      <UiPageHeader
        title="数据上传"
        breadcrumbs={[
          { key: "ingest", label: "采集与接收" },
          { key: "upload", label: "数据上传" },
        ]}
      />

      <nav className={styles.pageTabs} aria-label="数据上传页面" role="tablist">
        <Link
          ref={newUploadTabRef}
          to={dataUploadRoutes.newUpload}
          role="tab"
          aria-controls="new-upload-panel"
          aria-selected={activeTab === "new"}
          tabIndex={activeTab === "new" ? 0 : -1}
          onKeyDown={(event) =>
            handleTabKeyDown(event, dataUploadRoutes.records, recordsTabRef)
          }
        >
          新建上传
        </Link>
        <Link
          ref={recordsTabRef}
          to={dataUploadRoutes.records}
          role="tab"
          aria-controls="upload-records-panel"
          aria-selected={activeTab === "records"}
          tabIndex={activeTab === "records" ? 0 : -1}
          onKeyDown={(event) =>
            handleTabKeyDown(event, dataUploadRoutes.newUpload, newUploadTabRef)
          }
        >
          上传记录
        </Link>
      </nav>

      {activeTab === "new" ? (
        <section
          id="new-upload-panel"
          className={styles.tabPanel}
          role="tabpanel"
          aria-label="新建上传"
        >
          {!canManage ? (
            <Alert
              type="warning"
              showIcon
              title="当前为只读模式"
              description="可以查看 Manifest 预检结构和上传记录，但当前授权不能创建、暂停、恢复、重试或取消上传。"
            />
          ) : null}
          <div className={styles.uploadWorkspace}>
            <UploadMethodPanel
              sourceType={sourceType}
              files={files}
              objectStorageUri={objectStorageUri}
              projectId={scope.projectId}
              regionCode={scope.regionCode}
              preflight={preflight}
              disabled={!canManage}
              onSourceTypeChange={changeSourceType}
              onFilesChange={setFiles}
              onObjectStorageUriChange={setObjectStorageUri}
            />
            <ManifestPreflightPanel
              status={preflightStatus}
              preflight={preflight}
              problem={preflightProblem}
              onRetry={() => setPreflightNonce((value) => value + 1)}
            />
            <UploadQueuePanel
              items={queueItems}
              recovering={recovering}
              canManage={canManage}
              onPause={(id) => void pauseUpload(id)}
              onResume={(id) => void resumeUpload(id)}
              onRetry={(id) => void retryFailedParts(id)}
              onCancel={(id) => void cancelUpload(id)}
              onReattach={(id, file) => void reattachAndResume(id, file)}
              onClearSettled={clearSettled}
            />
          </div>

          <section
            className={styles.securityNotice}
            aria-labelledby="upload-security-heading"
          >
            <header>
              <ShieldCheck size={17} aria-hidden="true" />
              <h2 id="upload-security-heading">
                安全与操作提示 <small>（公网环境）</small>
              </h2>
            </header>
            <div>
              <article>
                <strong>完整性</strong>
                <span>
                  Manifest 预检只验证声明；提交时服务端再核验 SHA-256、CRC64
                  与对象大小。
                </span>
              </article>
              <article>
                <strong>大小限制</strong>
                <span>
                  Manifest ≤ 1 MiB；单包与文件声明合计 ≤ 5 TiB；最多 10,000
                  个分片。
                </span>
              </article>
              <article>
                <strong>支持格式</strong>
                <span>
                  V1 要求且仅允许 1 个 RAW_MCAP；Manifest 最多 256
                  个文件声明、128 路相机。
                </span>
              </article>
              <article>
                <strong>敏感信息与审计</strong>
                <span>
                  禁止上传凭据、密钥或个人隐私；预检、暂停、恢复、取消和提交均记录请求事实。
                </span>
              </article>
            </div>
          </section>

          <footer className={styles.uploadActions}>
            <div>
              <span
                className={
                  online ? styles.networkOnline : styles.networkOffline
                }
              >
                {online ? (
                  <Wifi size={13} aria-hidden="true" />
                ) : (
                  <WifiOff size={13} aria-hidden="true" />
                )}
                {online ? "公网连接可用" : "网络已断开"}
              </span>
              <FileJson2 size={14} aria-hidden="true" />
              <span>
                {startBlockedReason ??
                  `预检已通过：${preflight?.identifiers.data_package_id}`}
              </span>
            </div>
            <Button onClick={clearSettled}>清理已完成</Button>
            <Button
              type="primary"
              icon={<ArrowUp size={15} />}
              disabled={Boolean(startBlockedReason)}
              onClick={beginUpload}
            >
              开始上传
            </Button>
          </footer>
        </section>
      ) : (
        <section
          id="upload-records-panel"
          className={styles.tabPanel}
          role="tabpanel"
          aria-label="上传记录"
        >
          <UploadRecordsPanel
            items={records.data?.items ?? []}
            total={records.data?.total ?? 0}
            loading={records.isPending || records.isFetching}
            problem={records.isError ? uploadProblemCopy(records.error) : null}
            packageFilter={packageFilter}
            statusFilter={statusFilter}
            onPackageFilterChange={setPackageFilter}
            onStatusFilterChange={setStatusFilter}
            onRefresh={() => void records.refetch()}
          />
        </section>
      )}
    </main>
  );
}
