import { useQuery } from "@tanstack/react-query";
import { Alert, Modal } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { dataUploadRoutes } from "../../app/shell/navigation-routes";
import { useIngestScope } from "../../features/ingest/use-ingest-scope";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState, PageHeader } from "../../shared/ui";
import { UploadConfirmationDialog } from "./components/UploadConfirmationDialog";
import { LeRobotUploadPanel } from "./components/LeRobotUploadPanel";
import {
  UploadMethodPanel,
  type BrowserSelectionMode,
} from "./components/UploadMethodPanel";
import { UploadPrecheckPanel } from "./components/UploadPrecheckPanel";
import { UploadQueuePanel } from "./components/UploadQueuePanel";
import { UploadRecordsPanel } from "./components/UploadRecordsPanel";
import {
  listFormalUploadSessions,
  preflightUploadManifest,
  type UploadStatus,
} from "./formal-client";
import styles from "./styles.module.css";
import {
  inspectLocalUploadSelection,
  type LocalUploadSelection,
  type ServerCheckedUploadUnit,
  type UploadFlowState,
} from "./upload-flow";
import { uploadProblemCopy } from "./upload-contract";
import {
  uploadNativeLeRobot,
  type LeRobotTargetBinding,
} from "./lerobot-client";
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

function queueFlowPhase(
  items: ReturnType<typeof useUploadQueueStore.getState>["items"],
): "queue_ready" | "uploading" | "completed" | null {
  if (items.length === 0) return null;
  if (
    items.every((item) =>
      ["committed", "cancelled"].includes(item.transferStatus),
    )
  )
    return "completed";
  if (
    items.some((item) =>
      ["waiting", "preparing", "uploading", "pausing", "finalizing"].includes(
        item.transferStatus,
      ),
    )
  )
    return "uploading";
  return "queue_ready";
}

function folderNameFromFiles(files: readonly File[]): string {
  const path = files[0]?.webkitRelativePath;
  return path?.split("/")[0] || "所选文件";
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
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const location = useLocation();
  const navigate = useNavigate();
  const newUploadTabRef = useRef<HTMLAnchorElement>(null);
  const recordsTabRef = useRef<HTMLAnchorElement>(null);
  const localInspectionNonce = useRef(0);
  const activeTab =
    location.pathname === dataUploadRoutes.records ? "records" : "new";
  const online = useNetworkStatus();

  const [flow, setFlow] = useState<UploadFlowState>({ phase: "idle" });
  const [packageFilter, setPackageFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<UploadStatus>();
  const [scopeRequiredOpen, setScopeRequiredOpen] = useState(false);

  const queueItems = useUploadQueueStore((state) => state.items);
  const recovering = useUploadQueueStore((state) => state.recovering);
  const recoveryProblem = useUploadQueueStore((state) => state.recoveryProblem);
  const recoverQueue = useUploadQueueStore((state) => state.recover);
  const prepareUpload = useUploadQueueStore((state) => state.prepare);
  const beginPreparedUpload = useUploadQueueStore(
    (state) => state.beginPrepared,
  );
  const folderBatch = useUploadQueueStore((state) => state.folderBatch);
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
    if (
      flow.phase === "folder_selected" ||
      flow.phase === "confirming" ||
      flow.phase === "prechecking" ||
      flow.phase === "precheck_failed" ||
      flow.phase === "lerobot_uploading" ||
      flow.phase === "lerobot_failed" ||
      flow.phase === "lerobot_completed"
    )
      return;
    const next = queueFlowPhase(queueItems);
    if (next && next !== flow.phase) setFlow({ phase: next });
    if (!next && flow.phase !== "idle" && !recoveryProblem)
      setFlow({ phase: "idle" });
  }, [flow.phase, queueItems, recoveryProblem]);

  useEffect(() => {
    const hasActiveBrowserTransfer = queueItems.some(
      (item) =>
        item.sourceType === "BROWSER_MULTIPART" &&
        ["uploading", "pausing", "finalizing"].includes(item.transferStatus),
    );
    if (!hasActiveBrowserTransfer && flow.phase !== "lerobot_uploading") return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [flow.phase, queueItems]);

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

  const resetSelection = useCallback(() => {
    localInspectionNonce.current += 1;
    setFlow({ phase: "idle" });
  }, []);

  const abandonPreparedItems = useCallback(
    async (itemIds: readonly string[]) => {
      await Promise.allSettled(itemIds.map((itemId) => cancelUpload(itemId)));
      clearSettled();
      resetSelection();
    },
    [cancelUpload, clearSettled, resetSelection],
  );

  const confirmResetSelection = useCallback(
    (itemIds: readonly string[] = []) => {
      if (itemIds.length === 0) {
        resetSelection();
        return;
      }
      Modal.confirm({
        title: "取消已创建的上传任务？",
        content:
          "平台已创建部分上传任务，但尚未开始传输。返回重新选择将调用服务端取消接口并中止这些任务，且不能直接恢复。",
        okText: "取消任务并重新选择",
        cancelText: "继续处理",
        okButtonProps: { danger: true },
        onOk: () => abandonPreparedItems(itemIds),
      });
    },
    [abandonPreparedItems, resetSelection],
  );

  const inspectFiles = useCallback(
    async (nextFiles: readonly File[], mode: BrowserSelectionMode) => {
      const nonce = ++localInspectionNonce.current;
      if (nextFiles.length === 0) {
        setFlow({ phase: "idle" });
        return;
      }
      setFlow({
        phase: "folder_selected",
        folderName: folderNameFromFiles(nextFiles),
      });
      const selection = await inspectLocalUploadSelection({
        sourceType: "BROWSER_MULTIPART",
        browserSelectionMode: mode,
        files: nextFiles,
        objectStorageUri: "",
      });
      if (localInspectionNonce.current !== nonce) return;
      setFlow({ phase: "confirming", selection });
    },
    [],
  );

  const performServerPrecheck = useCallback(
    async (
      selection: LocalUploadSelection,
      previouslyPreparedItemIds: readonly string[] = [],
      lerobotBinding: LeRobotTargetBinding | null = null,
    ) => {
      if (!scope) {
        if (unscopedAccount) setScopeRequiredOpen(true);
        return;
      }
      if (!canManage || !online || selection.problems.length > 0) return;
      setFlow({
        phase: "folder_selected",
        folderName: selection.folderName,
      });
      const confirmedSelection = await inspectLocalUploadSelection({
        sourceType: selection.sourceType,
        browserSelectionMode: selection.browserSelectionMode,
        files: selection.files,
        objectStorageUri: selection.objectStorageUri,
      });
      if (confirmedSelection.lerobot) {
        if (!lerobotBinding) {
          setFlow({
            phase: "confirming",
            selection: confirmedSelection,
            preparedItemIds: previouslyPreparedItemIds,
          });
          return;
        }
        const initialProgress = {
          stage: "uploading" as const,
          currentPath: null,
          completedFiles: 0,
          totalFiles: confirmedSelection.lerobot.sourceFiles.length,
          uploadedBytes: 0,
          totalBytes: confirmedSelection.lerobot.sourceBytes,
        };
        setFlow({
          phase: "lerobot_uploading",
          selection: confirmedSelection,
          binding: lerobotBinding,
          progress: initialProgress,
        });
        try {
          const result = await uploadNativeLeRobot(
            scope,
            confirmedSelection.lerobot,
            lerobotBinding,
            (progress) =>
              setFlow({
                phase: "lerobot_uploading",
                selection: confirmedSelection,
                binding: lerobotBinding,
                progress,
              }),
          );
          setFlow({ phase: "lerobot_completed", result });
        } catch (error) {
          const copied = uploadProblemCopy(error);
          setFlow({
            phase: "lerobot_failed",
            selection: confirmedSelection,
            binding: lerobotBinding,
            problem: {
              ...copied,
              detail:
                copied.problemCode === null && error instanceof Error
                  ? error.message
                  : copied.detail,
            },
          });
        }
        return;
      }
      if (
        confirmedSelection.problems.length > 0 ||
        confirmedSelection.units.length === 0
      ) {
        setFlow({
          phase: "confirming",
          selection: confirmedSelection,
          preparedItemIds: previouslyPreparedItemIds,
        });
        return;
      }
      setFlow({
        phase: "prechecking",
        selection: confirmedSelection,
        stage: "submitting_manifest",
        completedUnits: 0,
      });
      const checked: ServerCheckedUploadUnit[] = [];
      const preparedIds = [...previouslyPreparedItemIds];
      try {
        for (const local of confirmedSelection.units) {
          setFlow({
            phase: "prechecking",
            selection: confirmedSelection,
            stage: "validating_manifest",
            completedUnits: checked.length,
          });
          const preflight = await preflightUploadManifest(
            scope,
            local.manifest,
          );
          checked.push({ local, preflight });
          setFlow({
            phase: "prechecking",
            selection: confirmedSelection,
            stage: "validating_manifest",
            completedUnits: checked.length,
          });
        }

        for (const [unitIndex, unit] of checked.entries()) {
          setFlow({
            phase: "prechecking",
            selection: confirmedSelection,
            stage: "creating_queue",
            completedUnits: unitIndex,
          });
          const itemId = await prepareUpload({
            scope,
            preflight: unit.preflight,
            sourceType: confirmedSelection.sourceType,
            files: unit.local.rawFile
              ? [unit.local.manifestFile, unit.local.rawFile]
              : [unit.local.manifestFile],
            objectStorageUri: confirmedSelection.objectStorageUri,
            idempotencyKey: `confirmed-upload:${unit.preflight.manifest_fingerprint}`,
            deferPartAuthorization: true,
          });
          if (!preparedIds.includes(itemId)) preparedIds.push(itemId);
          setFlow({
            phase: "prechecking",
            selection: confirmedSelection,
            stage: "creating_queue",
            completedUnits: unitIndex + 1,
          });
        }

        setFlow({ phase: "queue_ready" });
        void (async () => {
          for (const itemId of preparedIds) {
            await beginPreparedUpload(itemId);
          }
        })();
      } catch (error) {
        const problem = uploadProblemCopy(error);
        setFlow({
          phase: "precheck_failed",
          selection: confirmedSelection,
          problem,
          preparedItemIds: preparedIds,
        });
      }
    },
    [
      beginPreparedUpload,
      canManage,
      online,
      prepareUpload,
      scope,
      unscopedAccount,
    ],
  );

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

  if (!scope && !unscopedAccount) {
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
  if (!canRead && !unscopedAccount) {
    return (
      <main className={styles.page}>
        <PageState state="forbidden" label="数据上传" />
      </main>
    );
  }

  const recoveryFailureVisible =
    flow.phase === "idle" && recoveryProblem !== null;
  const selectionVisible =
    !recoveryFailureVisible &&
    (flow.phase === "idle" ||
      flow.phase === "folder_selected" ||
      flow.phase === "confirming");
  const precheckVisible =
    flow.phase === "prechecking" || flow.phase === "precheck_failed";
  const lerobotVisible =
    flow.phase === "lerobot_uploading" ||
    flow.phase === "lerobot_failed" ||
    flow.phase === "lerobot_completed";
  const queueVisible =
    flow.phase === "queue_ready" ||
    flow.phase === "uploading" ||
    flow.phase === "completed" ||
    recoveryFailureVisible;
  const failedPrecheckItemIds =
    flow.phase === "precheck_failed" ? flow.preparedItemIds : [];

  return (
    <main className={styles.page} data-upload-flow={flow.phase}>
      <PageHeader
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
          {!canManage && !unscopedAccount && selectionVisible ? (
            <Alert
              type="warning"
              showIcon
              title="当前账号缺少上传权限"
              description="你仍可选择文件夹并查看本地识别结果，但“确认上传”会保持禁用，不会创建任务或执行服务端预检。"
            />
          ) : null}

          {selectionVisible ? (
            <div className={styles.serialWorkspace}>
              <UploadMethodPanel
                disabled={flow.phase === "folder_selected"}
                scopeRequired={unscopedAccount}
                localChecking={flow.phase === "folder_selected"}
                onFilesChange={(nextFiles, mode) =>
                  void inspectFiles(nextFiles, mode)
                }
                onScopeRequired={() => setScopeRequiredOpen(true)}
              />
              <div className={styles.idleQueueHint} aria-live="polite">
                <strong>上传队列为空</strong>
                <span>确认上传并通过服务端预检后，队列才会开始处理。</span>
              </div>
              {flow.phase === "confirming" && scope ? (
                <UploadConfirmationDialog
                  open
                  selection={flow.selection}
                  scope={scope}
                  canManage={canManage}
                  online={online}
                  onCancel={() => confirmResetSelection(flow.preparedItemIds)}
                  onConfirm={(binding) =>
                    void performServerPrecheck(
                      flow.selection,
                      flow.preparedItemIds,
                      binding,
                    )
                  }
                />
              ) : null}
            </div>
          ) : null}

          {precheckVisible ? (
            <div className={styles.serialWorkspace}>
              <UploadPrecheckPanel
                state={flow}
                onRetry={() =>
                  void performServerPrecheck(
                    flow.selection,
                    failedPrecheckItemIds,
                  )
                }
                onModify={() =>
                  setFlow({
                    phase: "confirming",
                    selection: flow.selection,
                    preparedItemIds: failedPrecheckItemIds,
                  })
                }
                onBack={() => confirmResetSelection(failedPrecheckItemIds)}
              />
            </div>
          ) : null}

          {lerobotVisible ? (
            <div className={styles.serialWorkspace}>
              <LeRobotUploadPanel
                state={flow}
                onRetry={() => {
                  if (flow.phase === "lerobot_failed")
                    void performServerPrecheck(
                      flow.selection,
                      [],
                      flow.binding,
                    );
                }}
                onBack={resetSelection}
                onContinue={resetSelection}
                onViewRecords={() => void navigate(dataUploadRoutes.records)}
              />
            </div>
          ) : null}

          {queueVisible &&
          !precheckVisible &&
          !selectionVisible &&
          !lerobotVisible ? (
            <div className={styles.serialWorkspace}>
              <UploadQueuePanel
                items={queueItems}
                recovering={recovering}
                recoveryProblem={recoveryProblem}
                folderBatch={folderBatch}
                canManage={canManage}
                onPause={(id) => void pauseUpload(id)}
                onResume={(id) => void resumeUpload(id)}
                onRetry={(id) => void retryFailedParts(id)}
                onCancel={(id) => void cancelUpload(id)}
                onReattach={(id, file) => void reattachAndResume(id, file)}
                onClearSettled={clearSettled}
                onContinueUpload={() => {
                  clearSettled();
                  resetSelection();
                }}
                onViewRecords={() => void navigate(dataUploadRoutes.records)}
                onRecover={() => {
                  if (scope) void recoverQueue(scope);
                }}
              />
            </div>
          ) : null}
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
            loading={
              Boolean(scope) && (records.isPending || records.isFetching)
            }
            problem={records.isError ? uploadProblemCopy(records.error) : null}
            packageFilter={packageFilter}
            statusFilter={statusFilter}
            onPackageFilterChange={setPackageFilter}
            onStatusFilterChange={setStatusFilter}
            onRefresh={() => {
              if (scope) void records.refetch();
            }}
          />
        </section>
      )}

      <Modal
        cancelText="暂不上传"
        okText="前往账户设置"
        open={scopeRequiredOpen}
        title="上传前需要加入组织和项目"
        onCancel={() => setScopeRequiredOpen(false)}
        onOk={() => void navigate("/account/settings?tab=memberships")}
      >
        <p>
          当前账号尚未关联组织、项目和区域，平台无法确定数据应写入哪个存储位置，因此暂时不能创建上传任务。
        </p>
      </Modal>
    </main>
  );
}
