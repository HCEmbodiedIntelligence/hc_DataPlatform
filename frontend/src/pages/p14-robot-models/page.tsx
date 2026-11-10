import { Alert, Button, Drawer, Input, Modal, Spin } from "antd";
import {
  Box,
  CheckCircle2,
  CircleDot,
  File as FileIcon,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { authorizeRobotModelViewerAssets } from "../../features/robot-models/api/client";
import {
  useScopedDeleteRobotModel,
  useScopedRobotModelAssets,
  useScopedRobotModelJointMappings,
  useScopedRobotModels,
  useScopedRobotModelVersion,
} from "../../features/robot-models/api/hooks";
import { RobotModelImportDialog } from "../../features/robot-models/importing/RobotModelImportDialog";
import { useRobotModelImport } from "../../features/robot-models/importing/use-robot-model-import";
import {
  createLazyThreeRobotSceneLoader,
  RobotSceneCore,
} from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  CursorPager,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import workspace from "../../shared/ui/workspace.module.css";
import { robotModelsQueryCodec } from "./query-codec";
import type { RobotModel } from "../../entities/robot-model";
import { ModelVersionDetails } from "./ModelVersionDetails";
import styles from "./robot-assets.module.css";

function SummaryItem({
  icon,
  label,
  value,
}: Readonly<{ icon: React.ReactNode; label: string; value: string }>) {
  return (
    <section className={workspace.summaryItem} aria-label={label}>
      <span className={workspace.summaryIcon}>{icon}</span>
      <span className={workspace.summaryCopy}>
        <span>{label}</span>
        <strong>{value}</strong>
      </span>
    </section>
  );
}

export function Component() {
  const viewerScope = useShellStore((state) => state.scope);
  const [params, setParams] = useSearchParams();
  const search = robotModelsQueryCodec.parse(params);
  const organizationId = useShellStore(
    (state) =>
      state.scope?.organizationId ??
      state.sessionOrganizations[0]?.organizationId ??
      state.sessionScopes[0]?.organizationId ??
      null,
  );
  const bootstrapLoaded = useShellStore((state) => state.bootstrapLoaded);
  const capabilities = useOrganizationCapabilities(organizationId);
  const canManageModels = capabilities.has("robot_model.manage");

  const deletion = useScopedDeleteRobotModel(organizationId);
  const [deleteTarget, setDeleteTarget] = useState<{
    organizationId: string;
    model: RobotModel;
  } | null>(null);
  const [query, setQuery] = useState(search.q ?? "");

  const models = useScopedRobotModels(organizationId, {
    ...(search.q ? { q: search.q } : {}),
    ...(search.after ? { after: search.after } : {}),
    ...(search.before ? { before: search.before } : {}),
    limit: search.limit,
  });
  const modelItems = models.data?.items ?? [];
  const publishedCount = modelItems.filter(
    (model) => model.currentPublishedVersionId,
  ).length;
  const listedModel =
    modelItems.find((model) => model.id === search.modelId) ?? modelItems[0];
  const selectedVersionId =
    search.versionId ?? listedModel?.currentPublishedVersionId ?? null;
  const currentVersion = useScopedRobotModelVersion(
    organizationId,
    selectedVersionId,
  );
  const selectedModel = search.versionId
    ? modelItems.find(
        (model) =>
          model.id === (search.modelId ?? currentVersion.data?.robotModelId),
      )
    : listedModel;
  const detailsVersionId =
    search.versionId ??
    (search.detailTab !== "overview" || search.targetRobotId
      ? selectedModel?.currentPublishedVersionId
      : null);
  const currentAssets = useScopedRobotModelAssets(
    organizationId,
    selectedVersionId,
  );
  const currentMappings = useScopedRobotModelJointMappings(
    organizationId,
    selectedVersionId,
  );
  const currentUrdf = currentAssets.data?.find(
    (asset) => asset.role === "URDF",
  );

  useEffect(() => {
    if (
      search.versionId ||
      !selectedModel ||
      search.modelId === selectedModel.id
    )
      return;
    setParams(
      robotModelsQueryCodec.build(
        {
          ...search,
          modelId: selectedModel.id,
        },
        search,
      ),
      { replace: true },
    );
  }, [search, selectedModel, setParams]);

  const currentModelRef = useMemo(
    () =>
      currentVersion.data
        ? {
            modelId: currentVersion.data.robotModelId,
            modelVersion: currentVersion.data.id,
          }
        : null,
    [currentVersion.data],
  );
  const currentJointMapping = useMemo(
    () =>
      Object.fromEntries(
        (currentMappings.data ?? []).map((mapping) => [
          mapping.source_joint_name,
          mapping.target_joint_name,
        ]),
      ),
    [currentMappings.data],
  );
  const currentRuntimeLoader = useMemo(() => {
    if (!currentVersion.data || !currentUrdf || !organizationId) return;
    const versionId = currentVersion.data.id;
    const modelId = currentVersion.data.robotModelId;
    const requiredJoints = (currentMappings.data ?? []).map(
      (mapping) => mapping.source_joint_name,
    );
    return createLazyThreeRobotSceneLoader(async (_props, signal) => {
      const viewerAssets = await authorizeRobotModelViewerAssets(
        organizationId,
        versionId,
        currentAssets.data ?? [],
        signal,
        {
          scope: viewerScope ?? undefined,
          onLoadProgress: _props.onLoadProgress,
        },
      );
      return {
        manifest: { modelId, modelVersion: versionId, requiredJoints },
        ...viewerAssets,
        background: "#f5f7fc",
      };
    });
  }, [
    currentVersion.data,
    currentAssets.data,
    currentMappings.data,
    currentUrdf,
    organizationId,
    viewerScope,
  ]);

  const selectModel = (modelId: string) => {
    setParams(
      robotModelsQueryCodec.build(
        {
          ...search,
          modelId,
          versionId: undefined,
          targetRobotId: undefined,
          detailTab: "overview",
        },
        search,
      ),
    );
  };

  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const importer = useRobotModelImport({
    organizationId,
    enabled: canManageModels,
    onFailureNotice: setPageNotice,
    onPublished: async (published) => {
      await models.refetch();
      selectModel(published.robotModelId);
    },
  });
  const beginImport = importer.beginImport;
  const loadingExistingModel = importer.loadingExistingModel;
  const editCurrentModel = async () => {
    if (!selectedModel || !currentVersion.data || !currentAssets.data) return;
    await importer.editModel(
      selectedModel,
      currentVersion.data,
      currentAssets.data,
      currentMappings.data ?? [],
    );
  };
  const pageState = !bootstrapLoaded ? (
    <PageState state="loading" label="机器人模型资产" />
  ) : !organizationId ? (
    <PageState
      state="empty"
      title="尚未加入组织"
      description="机器人模型是组织级通用资产；加入组织后即可查看，无需先选择项目。"
    />
  ) : models.isPending ? (
    <PageState state="loading" label="机器人模型资产" />
  ) : models.error && isDomainError(models.error) ? (
    <PageState
      state={models.error.httpStatus === 403 ? "forbidden" : "error"}
      onRetry={() => void models.refetch()}
    />
  ) : null;

  return (
    <main className={workspace.page} data-page-id="P14">
      <StandardPageScaffold
        header={{
          title: "机器人模型资产",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "models", label: "模型资产" },
          ],
          actions: (
            <Button
              type="primary"
              disabled={!canManageModels}
              onClick={() => beginImport(null, null)}
            >
              导入模型
            </Button>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Box aria-hidden="true" size={20} />}
              label="已发布模型"
              value={String(publishedCount)}
            />
            <SummaryItem
              icon={<FileIcon aria-hidden="true" size={20} />}
              label="当前版本"
              value={currentVersion.data?.versionLabel ?? "—"}
            />
            <SummaryItem
              icon={<CheckCircle2 aria-hidden="true" size={20} />}
              label="发布状态"
              value={publishedCount ? "可用" : "—"}
            />
            <SummaryItem
              icon={<CircleDot aria-hidden="true" size={20} />}
              label="模型存储"
              value="PostgreSQL"
            />
          </div>
        }
        filters={
          <FilterToolbar
            onApply={() =>
              setParams(
                robotModelsQueryCodec.build(
                  {
                    ...search,
                    q: query || undefined,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              )
            }
            onReset={() => {
              setQuery("");
              setParams(
                robotModelsQueryCodec.build(
                  {
                    ...search,
                    q: undefined,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              );
            }}
          >
            <label className={workspace.toolbarField}>
              <span>模型名称</span>
              <Input.Search
                className={workspace.toolbarSearch}
                value={query}
                placeholder="搜索模型名称、厂商或型号"
                enterButton={
                  <Button
                    aria-label="搜索模型"
                    icon={<Search aria-hidden="true" size={15} />}
                  />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={styles.contentStack}>
          {pageNotice ? (
            <Alert
              type="error"
              showIcon
              closable
              title={pageNotice}
              onClose={() => setPageNotice(null)}
            />
          ) : null}
          {modelItems.length === 0 ? (
            <section
              className={styles.emptyWorkspace}
              aria-label="模型资产空状态"
            >
              <span className={styles.emptyWorkspaceIcon}>
                <Box aria-hidden="true" size={30} />
              </span>
              <div>
                <h2>{search.q ? "没有匹配的模型" : "还没有模型资产"}</h2>
                <p>
                  {search.q
                    ? "请调整搜索条件后重试。"
                    : "导入模型文件后，可在这里查看模型及其发布版本。"}
                </p>
              </div>
              {!search.q ? (
                <Button
                  type="primary"
                  disabled={!canManageModels}
                  onClick={() => beginImport(null, null)}
                >
                  导入第一个模型
                </Button>
              ) : null}
            </section>
          ) : (
            <div className={styles.assetWorkspace}>
              <aside className={styles.assetList} aria-label="模型列表">
                <header className={styles.listHeader}>
                  <div>
                    <h2>模型</h2>
                    <p>选择模型查看信息与发布版本</p>
                  </div>
                  <span>{modelItems.length}</span>
                </header>
                <div className={styles.robotList} role="list">
                  {modelItems.map((model) => (
                    <button
                      key={model.id}
                      type="button"
                      className={`${styles.robotCard} ${
                        model.id === selectedModel?.id
                          ? styles.robotCardActive
                          : ""
                      }`}
                      aria-pressed={model.id === selectedModel?.id}
                      onClick={() => selectModel(model.id)}
                    >
                      <span className={styles.robotCardIcon}>
                        <Box aria-hidden="true" size={18} />
                      </span>
                      <span className={styles.robotCardCopy}>
                        <strong>{model.displayName}</strong>
                        <span>
                          {model.manufacturer} / {model.modelCode}
                        </span>
                      </span>
                      <StatusTag
                        status={
                          model.currentPublishedVersionId
                            ? "PUBLISHED"
                            : "DRAFT"
                        }
                        label={
                          model.currentPublishedVersionId ? "已发布" : "未发布"
                        }
                        tone={
                          model.currentPublishedVersionId
                            ? "success"
                            : "neutral"
                        }
                      />
                    </button>
                  ))}
                </div>
                {models.data ? (
                  <footer className={styles.listFooter}>
                    <CursorPager
                      pageInfo={{
                        startCursor: models.data.pageInfo.start_cursor,
                        endCursor: models.data.pageInfo.end_cursor,
                        hasPreviousPage: models.data.pageInfo.has_previous_page,
                        hasNextPage: models.data.pageInfo.has_next_page,
                      }}
                      onChange={(cursor) =>
                        setParams(
                          robotModelsQueryCodec.build(
                            { ...search, ...cursor },
                            search,
                          ),
                        )
                      }
                      windowLabel={`当前 ${modelItems.length} 个`}
                    />
                  </footer>
                ) : null}
              </aside>

              <section
                className={styles.assetDetail}
                aria-label="机器人模型详情"
              >
                <header className={styles.detailHeader}>
                  <div>
                    <span className={styles.detailEyebrow}>机器人模型资产</span>
                    <h2>{selectedModel?.displayName}</h2>
                    <p>
                      {selectedModel?.manufacturer} / {selectedModel?.modelCode}
                    </p>
                  </div>
                  <StatusTag
                    status={
                      selectedModel?.currentPublishedVersionId
                        ? "PUBLISHED"
                        : "DRAFT"
                    }
                    label={
                      selectedModel?.currentPublishedVersionId
                        ? "已发布"
                        : "未发布"
                    }
                    tone={
                      selectedModel?.currentPublishedVersionId
                        ? "success"
                        : "neutral"
                    }
                  />
                </header>

                <div className={styles.detailGrid}>
                  <section
                    className={styles.previewCard}
                    aria-label="三维模型预览"
                  >
                    <header className={styles.previewHeader}>
                      <div>
                        <h3>3D 模型预览</h3>
                        <p>拖动旋转，滚轮缩放</p>
                      </div>
                      <span>
                        {currentVersion.data?.versionLabel ?? "读取中"}
                      </span>
                    </header>
                    <div className={styles.previewStage}>
                      {Boolean(selectedVersionId) &&
                      (currentVersion.isPending || currentAssets.isPending) ? (
                        <div className={styles.emptyPreview} role="status">
                          <Spin />
                          <strong>正在读取机器人模型…</strong>
                        </div>
                      ) : currentModelRef && currentRuntimeLoader ? (
                        <RobotSceneCore
                          modelRef={currentModelRef}
                          jointMapping={currentJointMapping}
                          runtimeLoader={currentRuntimeLoader}
                        />
                      ) : (
                        <div className={styles.emptyPreview}>
                          <Box aria-hidden="true" size={42} />
                          <strong>模型暂时无法预览</strong>
                          <span>
                            请检查当前模型文件，或重新进入可视化检查流程。
                          </span>
                        </div>
                      )}
                    </div>
                  </section>

                  <aside className={styles.modelPanel} aria-label="模型信息">
                    <div className={styles.modelPanelHeader}>
                      <h3>模型信息</h3>
                      <span>PostgreSQL 持久化</span>
                    </div>
                    <dl className={styles.modelFacts}>
                      <div className={styles.modelFact}>
                        <dt>模型 ID</dt>
                        <dd title={selectedModel?.id}>
                          {selectedModel?.id ?? "—"}
                        </dd>
                      </div>
                      <div className={styles.modelFact}>
                        <dt>URDF</dt>
                        <dd title={currentUrdf?.relative_path}>
                          {currentUrdf?.relative_path ?? "读取中"}
                        </dd>
                      </div>
                      <div className={styles.modelFact}>
                        <dt>描述配置</dt>
                        <dd>
                          {currentAssets.data?.find(
                            (asset) => asset.role === "CONFIG",
                          )?.relative_path ?? "读取中"}
                        </dd>
                      </div>
                      <div className={styles.modelFact}>
                        <dt>关节映射</dt>
                        <dd>{currentMappings.data?.length ?? 0} 项</dd>
                      </div>
                    </dl>
                    <div className={styles.modelActions}>
                      <Button
                        block
                        icon={<FileIcon aria-hidden="true" size={16} />}
                        loading={loadingExistingModel}
                        disabled={
                          !canManageModels ||
                          !currentAssets.data?.length ||
                          currentMappings.isPending
                        }
                        onClick={() => void editCurrentModel()}
                      >
                        检查或更新模型
                      </Button>
                      <p>
                        更新会生成新版本并重新解析、预览和校验，不会直接覆盖当前发布版本。
                      </p>
                      <Button
                        block
                        disabled={!selectedVersionId}
                        onClick={() =>
                          setParams(
                            robotModelsQueryCodec.build(
                              {
                                ...search,
                                modelId: selectedModel?.id,
                                versionId: selectedVersionId ?? undefined,
                                detailTab: "assets",
                              },
                              search,
                            ),
                          )
                        }
                      >
                        版本管理
                      </Button>
                      <Button
                        block
                        danger
                        disabled={!canManageModels || !selectedModel}
                        aria-label={`删除模型 ${selectedModel?.displayName ?? ""}`}
                        onClick={() => {
                          if (!organizationId || !selectedModel) return;
                          deletion.reset();
                          setDeleteTarget({
                            organizationId,
                            model: selectedModel,
                          });
                        }}
                      >
                        删除模型
                      </Button>
                    </div>
                  </aside>
                </div>
              </section>
            </div>
          )}
        </div>
      </StandardPageScaffold>

      {deleteTarget && deleteTarget.organizationId === organizationId && (
        <Modal
          open
          title="删除机器人模型"
          okText="确认删除"
          cancelText="取消"
          okButtonProps={{ danger: true, disabled: !canManageModels }}
          confirmLoading={deletion.isPending}
          closable={!deletion.isPending}
          onCancel={() => {
            if (!deletion.isPending) setDeleteTarget(null);
          }}
          onOk={() => {
            if (!canManageModels || deletion.isPending) return;
            deletion.mutate(deleteTarget.model.id, {
              onSuccess: () => {
                setDeleteTarget(null);
                if (selectedModel?.id === deleteTarget.model.id) {
                  setParams(
                    robotModelsQueryCodec.build(
                      {
                        ...search,
                        modelId: undefined,
                        versionId: undefined,
                        detailTab: "overview",
                      },
                      search,
                    ),
                    { replace: true },
                  );
                }
              },
            });
          }}
        >
          <p>
            确认删除“{deleteTarget.model.displayName}
            ”及其全部版本和资产文件？此操作无法撤销。
          </p>
          {deletion.error && (
            <Alert
              type="error"
              showIcon
              title={
                isDomainError(deletion.error) &&
                deletion.error.problemCode === "ROBOT_MODEL_IN_USE"
                  ? "该模型仍被机器人、历史绑定或数据记录引用，无法删除。"
                  : deletion.error.message || "删除失败，请重试。"
              }
            />
          )}
        </Modal>
      )}
      {detailsVersionId && (
        <Drawer
          open
          title="模型版本管理"
          size="large"
          onClose={() =>
            setParams(
              robotModelsQueryCodec.build(
                {
                  ...search,
                  modelId: selectedModel?.id,
                  versionId: undefined,
                  targetRobotId: undefined,
                  detailTab: "overview",
                },
                search,
              ),
              { replace: true },
            )
          }
        >
          <ModelVersionDetails
            key={JSON.stringify([organizationId, detailsVersionId])}
            organizationId={organizationId}
            versionId={detailsVersionId}
          />
        </Drawer>
      )}
      <RobotModelImportDialog controller={importer} />
    </main>
  );
}

export default Component;
