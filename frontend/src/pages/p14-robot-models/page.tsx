import { Button, Input, Select, Space } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Box,
  Boxes,
  FileStack,
  Search,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RobotModel } from "../../entities/robot-model";
import {
  useRobotModelAssetDownload,
  authorizeRobotModelViewerAssets,
  useRobotModelAssets,
  useRobotModelBindings,
  useRobotModelJointMappings,
  useRobotModels,
  useRobotModelVersion,
  useBindRobotModelVersion,
  useLoadRobotBindingTarget,
  usePreflightRobotModelPublish,
  usePublishRobotModelVersion,
  useReplaceRobotModelJointMappings,
  useUploadRobotModelAssets,
  type RobotModelAssetUploadInput,
  type RobotModelJointMapping,
  type RobotModelPublishPreflight,
} from "../../features/robot-models/api";
import {
  createLazyThreeRobotSceneLoader,
  RobotSceneCore,
} from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { formatStorageSize } from "../../shared/lib/metric-presentation";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  DataCursorPager,
  DataTable,
  DetailTabs,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import workspace from "../ui-011e/workspace.module.css";
import { robotModelsQueryCodec } from "./query-codec";

const detailTabs = [
  { id: "overview", label: "概览" },
  { id: "assets", label: "资产文件" },
  { id: "mapping", label: "关节映射" },
  { id: "bindings", label: "绑定机器人" },
  { id: "validations", label: "校验记录" },
] as const;

const incompatibilityLabels = {
  JOINT_MAPPING: "Joint Mapping 与模型必需关节不匹配，已阻止加载错误 3D 事实。",
  MODEL_VERSION: "模型版本与查看器数据清单不匹配。",
  CALIBRATION_VERSION: "标定版本不匹配。",
  FRAME_GRAPH: "Frame Graph 不匹配。",
} as const;

const assetRoleForExtension: Record<
  string,
  Pick<RobotModelAssetUploadInput, "role" | "mediaType">
> = {
  urdf: { role: "URDF", mediaType: "application/xml" },
  xml: { role: "URDF", mediaType: "application/xml" },
  stl: { role: "MESH", mediaType: "model/stl" },
  obj: { role: "MESH", mediaType: "model/obj" },
  dae: { role: "MESH", mediaType: "model/vnd.collada+xml" },
  glb: { role: "MESH", mediaType: "model/gltf-binary" },
  gltf: { role: "MESH", mediaType: "model/gltf+json" },
  png: { role: "TEXTURE", mediaType: "image/png" },
  jpg: { role: "TEXTURE", mediaType: "image/jpeg" },
  jpeg: { role: "TEXTURE", mediaType: "image/jpeg" },
  webp: { role: "TEXTURE", mediaType: "image/webp" },
  ktx2: { role: "TEXTURE", mediaType: "image/ktx2" },
  json: { role: "CONFIG", mediaType: "application/json" },
  yaml: { role: "CONFIG", mediaType: "application/yaml" },
  yml: { role: "CONFIG", mediaType: "application/yaml" },
  toml: { role: "CONFIG", mediaType: "application/toml" },
  md: { role: "DOCUMENTATION", mediaType: "text/markdown" },
  txt: { role: "DOCUMENTATION", mediaType: "text/plain" },
  pdf: { role: "DOCUMENTATION", mediaType: "application/pdf" },
};

function assetMetadata(
  file: File,
): Pick<RobotModelAssetUploadInput, "role" | "mediaType"> | null {
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  return extension ? (assetRoleForExtension[extension] ?? null) : null;
}

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
  const [params, setParams] = useSearchParams();
  const search = robotModelsQueryCodec.parse(params);
  const [query, setQuery] = useState(search.q ?? "");
  const models = useRobotModels(search.q ? { q: search.q } : {});
  const version = useRobotModelVersion(search.versionId ?? null);
  const assets = useRobotModelAssets(version.data?.id ?? null);
  const bindings = useRobotModelBindings(version.data?.id ?? null);
  const jointMappings = useRobotModelJointMappings(version.data?.id ?? null);
  const assetDownload = useRobotModelAssetDownload();
  const assetUpload = useUploadRobotModelAssets();
  const replaceJointMappings = useReplaceRobotModelJointMappings();
  const bindRobotModelVersion = useBindRobotModelVersion();
  const loadRobotBindingTarget = useLoadRobotBindingTarget();
  const publishPreflight = usePreflightRobotModelPublish();
  const publishVersion = usePublishRobotModelVersion();
  const [incompatibleReason, setIncompatibleReason] = useState<string | null>(
    null,
  );
  const [selectedAssetFile, setSelectedAssetFile] = useState<File | null>(null);
  const [assetSha256, setAssetSha256] = useState("");
  const [mappingState, setMappingState] = useState<{
    readonly versionId: string | null;
    readonly dirty: boolean;
    readonly rows: readonly RobotModelJointMapping[];
  }>({ versionId: null, dirty: false, rows: [] });
  const [preflight, setPreflight] = useState<{
    readonly result: RobotModelPublishPreflight;
    readonly idempotencyKey: string;
  } | null>(null);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);
  const scope = useShellStore((state) => state.scope);
  const [bindingRegionCode, setBindingRegionCode] = useState(
    search.targetRegionCode ?? "",
  );
  const [bindingRobotId, setBindingRobotId] = useState(
    search.targetRobotId ?? "",
  );
  const [bindingTarget, setBindingTarget] = useState<{
    readonly robotId: string;
    readonly displayName: string;
    readonly etag: string;
  } | null>(null);

  const items = models.data?.items ?? [];
  const selectedModel =
    items.find((item) => item.id === search.modelId) ?? items[0];
  const selectedVersion = version.data;
  const bindingError =
    loadRobotBindingTarget.error ?? bindRobotModelVersion.error;
  const viewerJointMapping = useMemo(
    () =>
      Object.fromEntries(
        (jointMappings.data ?? []).map((mapping) => [
          mapping.source_joint_name,
          mapping.target_joint_name,
        ]),
      ),
    [jointMappings.data],
  );
  const urdfAsset = assets.data?.find((asset) => asset.role === "URDF");
  const runtimeLoader = useMemo(() => {
    if (!selectedVersion || !urdfAsset || !scope?.organizationId) return;
    const modelId = selectedVersion.robotModelId;
    const modelVersion = selectedVersion.id;
    const organizationId = scope.organizationId;
    const requiredJoints = (jointMappings.data ?? []).map(
      (mapping) => mapping.source_joint_name,
    );
    return createLazyThreeRobotSceneLoader(async (_props, signal) => {
      const viewerAssets = await authorizeRobotModelViewerAssets(
        organizationId,
        modelVersion,
        assets.data ?? [],
        signal,
      );
      return {
        manifest: { modelId, modelVersion, requiredJoints },
        ...viewerAssets,
      };
    });
  }, [
    assets.data,
    jointMappings.data,
    scope?.organizationId,
    selectedVersion,
    urdfAsset,
  ]);

  useEffect(() => {
    if (!selectedVersion || !jointMappings.data) return;
    setMappingState((previous) =>
      previous.versionId === selectedVersion.id && previous.dirty
        ? previous
        : {
            versionId: selectedVersion.id,
            dirty: false,
            rows: jointMappings.data,
          },
    );
  }, [jointMappings.data, selectedVersion]);

  useEffect(() => {
    if (!bindingRegionCode && scope?.regionCode) {
      setBindingRegionCode(scope.regionCode);
    }
  }, [bindingRegionCode, scope?.regionCode]);

  useEffect(() => {
    if (search.targetRobotId) setBindingRobotId(search.targetRobotId);
    if (search.targetRegionCode) setBindingRegionCode(search.targetRegionCode);
  }, [search.targetRegionCode, search.targetRobotId]);

  const downloadAsset = async (assetId: string) => {
    if (!selectedVersion) return;
    const authorization = await assetDownload.mutateAsync({
      versionId: selectedVersion.id,
      assetId,
    });
    window.location.assign(authorization.download_url);
  };

  const uploadAsset = async () => {
    if (!selectedVersion || !selectedAssetFile) return;
    const metadata = assetMetadata(selectedAssetFile);
    if (!metadata || !/^[0-9a-f]{64}$/u.test(assetSha256)) return;
    await assetUpload.mutateAsync({
      versionId: selectedVersion.id,
      idempotencyKey: crypto.randomUUID(),
      files: [
        {
          file: selectedAssetFile,
          relativePath: selectedAssetFile.name,
          role: metadata.role,
          mediaType: selectedAssetFile.type || metadata.mediaType,
          sha256: assetSha256,
        },
      ],
    });
    setSelectedAssetFile(null);
    setAssetSha256("");
    setPreflight(null);
    setPublishMessage(
      "资产已完成服务端 SHA-256 校验；请保存映射后运行发布预检。",
    );
  };

  const updateMappingRow = (
    index: number,
    patch: Partial<RobotModelJointMapping>,
  ) => {
    setMappingState((previous) => ({
      ...previous,
      dirty: true,
      rows: previous.rows.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    }));
    setPreflight(null);
  };

  const saveMappings = async () => {
    if (!selectedVersion) return;
    const rows = mappingState.rows.map((row) => ({
      ...row,
      source_joint_name: row.source_joint_name.trim(),
      target_joint_name: row.target_joint_name.trim(),
    }));
    if (rows.some((row) => !row.source_joint_name || !row.target_joint_name)) {
      setPublishMessage("每条关节映射都需要源关节和目标关节名称。");
      return;
    }
    const updated = await replaceJointMappings.mutateAsync({
      versionId: selectedVersion.id,
      etag: selectedVersion.etag,
      mappings: rows,
      idempotencyKey: crypto.randomUUID(),
    });
    setMappingState({ versionId: updated.id, dirty: false, rows });
    setPreflight(null);
    setPublishMessage("关节映射已保存；可运行发布预检。");
  };

  const runPublishPreflight = async () => {
    if (!selectedVersion || mappingState.dirty) return;
    const idempotencyKey = crypto.randomUUID();
    const result = await publishPreflight.mutateAsync({
      versionId: selectedVersion.id,
      etag: selectedVersion.etag,
      idempotencyKey,
    });
    setPreflight({ result, idempotencyKey });
    setPublishMessage(
      result.allowed
        ? "发布预检通过；可在令牌有效期内发布该版本。"
        : "发布预检未通过；请处理列出的阻断项后重新运行。",
    );
  };

  const publish = async () => {
    if (!selectedVersion || !preflight?.result.preflight_token) return;
    const published = await publishVersion.mutateAsync({
      versionId: selectedVersion.id,
      etag: selectedVersion.etag,
      idempotencyKey: preflight.idempotencyKey,
      preflightToken: preflight.result.preflight_token,
    });
    setPreflight(null);
    setPublishMessage(`版本 ${published.versionLabel} 已发布。`);
    if (search.targetRobotId) {
      setParams(
        robotModelsQueryCodec.build(
          { ...search, detailTab: "bindings", versionId: published.id },
          search,
        ),
      );
    }
  };

  const loadBindingTarget = async () => {
    if (
      !scope?.projectId ||
      !bindingRegionCode.trim() ||
      !bindingRobotId.trim()
    )
      return;
    const target = await loadRobotBindingTarget.mutateAsync({
      projectId: scope.projectId,
      regionCode: bindingRegionCode.trim(),
      robotId: bindingRobotId.trim(),
    });
    setBindingTarget(target);
    setPublishMessage(`已读取机器人 ${target.displayName} 的当前版本标签。`);
  };

  const bindRobot = async () => {
    if (!selectedVersion || !bindingTarget || !bindingRegionCode.trim()) return;
    const binding = await bindRobotModelVersion.mutateAsync({
      versionId: selectedVersion.id,
      regionCode: bindingRegionCode.trim(),
      robotId: bindingTarget.robotId,
      robotEtag: bindingTarget.etag,
      idempotencyKey: crypto.randomUUID(),
    });
    setBindingTarget(null);
    setBindingRobotId("");
    setPublishMessage(`已将版本绑定到机器人 ${binding.robot_id}。`);
  };

  const columns = useMemo<ColumnDef<RobotModel, unknown>[]>(
    () => [
      {
        id: "name",
        header: "模型",
        size: 170,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              setParams(
                robotModelsQueryCodec.build(
                  {
                    ...search,
                    modelId: row.original.id,
                    ...(row.original.currentPublishedVersionId
                      ? { versionId: row.original.currentPublishedVersionId }
                      : {}),
                  },
                  search,
                ),
              )
            }
          >
            {row.original.displayName}
          </Button>
        ),
      },
      {
        id: "manufacturer",
        header: "厂商",
        size: 120,
        cell: ({ row }) => row.original.manufacturer,
      },
      {
        id: "modelCode",
        header: "型号",
        size: 110,
        cell: ({ row }) => row.original.modelCode,
      },
      {
        id: "version",
        header: "当前版本",
        size: 180,
        cell: ({ row }) => row.original.currentPublishedVersionId ?? "未发布",
      },
      {
        id: "binding",
        header: "绑定状态",
        size: 100,
        cell: ({ row }) => (
          <StatusTag
            status={
              row.original.currentPublishedVersionId ? "BOUND" : "UNBOUND"
            }
            label={row.original.currentPublishedVersionId ? "已发布" : "未发布"}
            tone={
              row.original.currentPublishedVersionId ? "success" : "neutral"
            }
          />
        ),
      },
    ],
    [search, setParams],
  );

  const pageState = models.isPending ? (
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
          description: "在固定版本上管理可审计资产、关节映射和一次性发布预检。",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "models", label: "机器人模型资产" },
          ],
          actions: (
            <Space wrap>
              <Button
                type="primary"
                href="/settings/robots"
                icon={<Upload aria-hidden="true" size={16} />}
              >
                导入 URDF / 配置文件
              </Button>
              <Button
                disabled={selectedVersion?.lifecycle !== "DRAFT"}
                onClick={() =>
                  setParams(
                    robotModelsQueryCodec.build(
                      { ...search, detailTab: "assets" },
                      search,
                    ),
                  )
                }
              >
                {selectedVersion?.lifecycle === "DRAFT"
                  ? "管理资产文件"
                  : "选择草稿版本以管理资产"}
              </Button>
            </Space>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="模型总数"
              value={String(items.length)}
            />
            <SummaryItem
              icon={<FileStack size={20} />}
              label="已发布版本"
              value={String(
                items.filter((item) => item.currentPublishedVersionId).length,
              )}
            />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="安全模式"
              value="固定 ID"
            />
            <SummaryItem
              icon={<Box size={20} />}
              label="资产传输"
              value="短期直传授权"
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
                    binding: "all",
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
                placeholder="搜索模型名称"
                enterButton={
                  <Button
                    aria-label="搜索模型名称"
                    icon={<Search aria-hidden="true" size={15} />}
                  />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>绑定状态</span>
              <Select
                value={search.binding}
                options={[
                  { value: "all", label: "全部" },
                  { value: "bound", label: "已绑定" },
                  { value: "unbound", label: "未绑定" },
                ]}
                onChange={(binding) =>
                  setParams(
                    robotModelsQueryCodec.build(
                      {
                        ...search,
                        binding,
                        after: undefined,
                        before: undefined,
                      },
                      search,
                    ),
                  )
                }
              />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={workspace.twoPane}>
          <section className={workspace.pane} aria-label="机器人模型列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>模型资产</h2>
                <p>列表只展示授权范围内的稳定资源</p>
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(model) => model.id}
                caption="机器人模型资产"
                state={items.length ? "ready" : "empty"}
                empty={
                  <PageState state={search.q ? "filtered-empty" : "empty"} />
                }
              />
            </div>
            {models.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
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
                  windowLabel={`当前 ${items.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <aside className={workspace.inspector} aria-label="模型版本详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>{selectedModel?.displayName ?? "固定版本详情"}</h2>
                <p>
                  {selectedVersion
                    ? selectedVersion.versionLabel
                    : "列表事实 / 未加载固定版本"}
                </p>
              </div>
              <StatusTag
                status={
                  selectedVersion?.lifecycle ??
                  (selectedModel ? "LISTED" : "UNKNOWN")
                }
                label={
                  selectedVersion?.lifecycle ??
                  (selectedModel ? "已收录" : "未知状态")
                }
                tone={
                  selectedVersion?.lifecycle === "PUBLISHED" || selectedModel
                    ? "success"
                    : "warning"
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <div className={workspace.visualStage}>
                {selectedVersion ? (
                  <RobotSceneCore
                    modelRef={{
                      modelId: selectedVersion.robotModelId,
                      modelVersion: selectedVersion.id,
                    }}
                    jointMapping={viewerJointMapping}
                    runtimeLoader={runtimeLoader}
                    onIncompatible={(reason) =>
                      setIncompatibleReason(incompatibilityLabels[reason])
                    }
                  />
                ) : (
                  <div className={workspace.visualStageCopy}>
                    <Box aria-hidden="true" size={48} />
                    <strong>
                      {selectedModel?.modelCode ?? "选择固定模型版本"}
                    </strong>
                    <span>
                      3D 资源只在 URL 携带稳定 versionId
                      且合同通过后加载，不自动回退到 latest/current。
                    </span>
                  </div>
                )}
              </div>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>模型 ID</dt>
                  <dd>
                    <code>{selectedModel?.id ?? "—"}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>厂商 / 型号</dt>
                  <dd>
                    {selectedModel
                      ? `${selectedModel.manufacturer} / ${selectedModel.modelCode}`
                      : "—"}
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>固定版本</dt>
                  <dd>
                    <code>
                      {selectedVersion?.id ??
                        selectedModel?.currentPublishedVersionId ??
                        "未发布"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>资源可用性</dt>
                  <dd>
                    {selectedVersion?.assetAvailability ?? "需加载固定版本"}
                  </dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs}
                activeTab={search.detailTab}
                panelIdForTab={(tabId) => `p14-tabpanel-${tabId}`}
                onChange={(detailTab) =>
                  setParams(
                    robotModelsQueryCodec.build(
                      {
                        ...search,
                        detailTab: detailTab as typeof search.detailTab,
                      },
                      search,
                    ),
                  )
                }
              />
              {detailTabs.map((tab) => (
                <section
                  key={tab.id}
                  className={workspace.tabContent}
                  role="tabpanel"
                  id={`p14-tabpanel-${tab.id}`}
                  aria-labelledby={`tab-${tab.id}`}
                  hidden={search.detailTab !== tab.id}
                >
                  {tab.id === "overview" ? (
                    <p className={workspace.safeNote}>
                      浏览器直传授权仅驻留内存且 no-store；Multipart ETag 与内容
                      SHA-256 不等价。
                    </p>
                  ) : null}
                  {tab.id === "assets" ? (
                    assets.isPending ? (
                      <PageState state="loading" label="固定版本资产" />
                    ) : assets.error ? (
                      <PageState
                        state="error"
                        onRetry={() => void assets.refetch()}
                      />
                    ) : !selectedVersion ? (
                      <PageState
                        state="empty"
                        title="选择固定版本后查看资产"
                        description="资产清单始终绑定到一个不可变的版本，不回退到 latest。"
                      />
                    ) : assets.data?.length ? (
                      <ul
                        className={workspace.factList}
                        aria-label="固定版本资产清单"
                      >
                        {assets.data.map((asset) => (
                          <li
                            className={workspace.factRow}
                            key={asset.asset_id}
                          >
                            <span>
                              <strong>{asset.relative_path}</strong>
                              <small>
                                {asset.role} · {asset.media_type} ·{" "}
                                {formatStorageSize(asset.size_bytes)}
                              </small>
                            </span>
                            <Button
                              loading={
                                assetDownload.isPending &&
                                assetDownload.variables?.assetId ===
                                  asset.asset_id
                              }
                              onClick={() => void downloadAsset(asset.asset_id)}
                            >
                              下载
                            </Button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <PageState
                        state="empty"
                        title="这个固定版本还没有完成的资产"
                        description="上传会先取得短期分片授权；完成后服务端重新读取对象并验证 SHA-256。"
                      />
                    )
                  ) : null}
                  {tab.id === "assets" &&
                  selectedVersion?.lifecycle === "DRAFT" ? (
                    <section
                      className={workspace.featureNote}
                      aria-label="上传固定版本资产"
                    >
                      <strong>上传资产</strong>
                      <p>
                        浏览器只保留短期分片授权；请提供构建产物的
                        SHA-256，客户端不会把大文件完整读入内存。
                      </p>
                      <div className={workspace.actionRow}>
                        <Button href="/robots/annotation-demo/robot.urdf">
                          下载内置 7 轴测试 URDF
                        </Button>
                        <Button href="/robots/annotation-demo/robot.config.json">
                          下载测试配置文件
                        </Button>
                      </div>
                      <label className={workspace.toolbarField}>
                        <span>资产文件</span>
                        <input
                          type="file"
                          onChange={(event) =>
                            setSelectedAssetFile(
                              event.target.files?.item(0) ?? null,
                            )
                          }
                        />
                      </label>
                      {selectedAssetFile &&
                      !assetMetadata(selectedAssetFile) ? (
                        <p className={workspace.warningNote} role="alert">
                          该扩展名不属于受支持的
                          URDF、网格、纹理、配置或文档资产。
                        </p>
                      ) : null}
                      <label className={workspace.toolbarField}>
                        <span>SHA-256（小写十六进制）</span>
                        <Input
                          value={assetSha256}
                          maxLength={64}
                          autoComplete="off"
                          onChange={(event) =>
                            setAssetSha256(event.target.value.trim())
                          }
                        />
                      </label>
                      {assetUpload.error ? (
                        <p className={workspace.warningNote} role="alert">
                          {isDomainError(assetUpload.error)
                            ? assetUpload.error.message
                            : "资产上传未完成；可使用同一文件重新发起上传。"}
                        </p>
                      ) : null}
                      <Button
                        type="primary"
                        loading={assetUpload.isPending}
                        disabled={
                          !selectedAssetFile ||
                          !assetMetadata(selectedAssetFile) ||
                          !/^[0-9a-f]{64}$/u.test(assetSha256)
                        }
                        onClick={() => void uploadAsset()}
                      >
                        上传并验证
                      </Button>
                    </section>
                  ) : null}
                  {tab.id === "mapping" ? (
                    !selectedVersion ? (
                      <PageState
                        state="empty"
                        title="选择草稿版本后管理关节映射"
                        description="映射始终绑定到一个固定版本，不会应用到 latest。"
                      />
                    ) : jointMappings.isPending ? (
                      <PageState state="loading" label="关节映射" />
                    ) : jointMappings.error ? (
                      <PageState
                        state="error"
                        onRetry={() => void jointMappings.refetch()}
                      />
                    ) : selectedVersion.lifecycle !== "DRAFT" ? (
                      <p className={workspace.safeNote}>
                        已发布版本的关节映射不可变。请创建新的草稿版本后进行修改。
                      </p>
                    ) : (
                      <section
                        className={workspace.featureNote}
                        aria-label="关节映射编辑器"
                      >
                        <strong>关节映射</strong>
                        <p>
                          源关节必须与 URDF
                          的活动关节精确一致；发布预检会验证完整性。
                        </p>
                        {mappingState.rows.map((mapping, index) => (
                          <div
                            className={workspace.actionRow}
                            key={`${index}-${mapping.source_joint_name}`}
                          >
                            <Input
                              aria-label={`源关节 ${index + 1}`}
                              value={mapping.source_joint_name}
                              placeholder="URDF 源关节"
                              onChange={(event) =>
                                updateMappingRow(index, {
                                  source_joint_name: event.target.value,
                                })
                              }
                            />
                            <Input
                              aria-label={`目标关节 ${index + 1}`}
                              value={mapping.target_joint_name}
                              placeholder="目标执行关节"
                              onChange={(event) =>
                                updateMappingRow(index, {
                                  target_joint_name: event.target.value,
                                })
                              }
                            />
                            <Select
                              aria-label={`方向 ${index + 1}`}
                              value={mapping.direction}
                              options={[
                                { value: "SAME", label: "同向" },
                                { value: "INVERTED", label: "反向" },
                              ]}
                              onChange={(direction) =>
                                updateMappingRow(index, { direction })
                              }
                            />
                            <Button
                              onClick={() => {
                                setMappingState((previous) => ({
                                  ...previous,
                                  dirty: true,
                                  rows: previous.rows.filter(
                                    (_row, rowIndex) => rowIndex !== index,
                                  ),
                                }));
                                setPreflight(null);
                              }}
                            >
                              移除
                            </Button>
                          </div>
                        ))}
                        <div className={workspace.actionRow}>
                          <Button
                            onClick={() => {
                              setMappingState((previous) => ({
                                ...previous,
                                dirty: true,
                                rows: [
                                  ...previous.rows,
                                  {
                                    source_joint_name: "",
                                    target_joint_name: "",
                                    direction: "SAME",
                                  },
                                ],
                              }));
                              setPreflight(null);
                            }}
                          >
                            添加关节映射
                          </Button>
                          <Button
                            type="primary"
                            loading={replaceJointMappings.isPending}
                            onClick={() => void saveMappings()}
                          >
                            保存映射
                          </Button>
                        </div>
                        {replaceJointMappings.error ? (
                          <p className={workspace.warningNote} role="alert">
                            {isDomainError(replaceJointMappings.error)
                              ? replaceJointMappings.error.message
                              : "映射未保存；请重新加载版本后重试。"}
                          </p>
                        ) : null}
                      </section>
                    )
                  ) : null}
                  {tab.id === "bindings" ? (
                    !selectedVersion ? (
                      <PageState
                        state="empty"
                        title="选择固定版本后查看绑定"
                        description="每条绑定都指向明确的机器人、区域和已发布模型版本。"
                      />
                    ) : bindings.isPending ? (
                      <PageState state="loading" label="机器人绑定" />
                    ) : bindings.error ? (
                      <PageState
                        state="error"
                        onRetry={() => void bindings.refetch()}
                      />
                    ) : (
                      <section
                        className={workspace.featureNote}
                        aria-label="机器人模型绑定"
                      >
                        <strong>机器人绑定</strong>
                        {bindings.data?.length ? (
                          <ul
                            className={workspace.factList}
                            aria-label="绑定历史"
                          >
                            {bindings.data.map((binding) => (
                              <li
                                className={workspace.factRow}
                                key={binding.binding_id}
                              >
                                <span>
                                  <strong>{binding.robot_id}</strong>
                                  <small>
                                    {binding.region_code} · {binding.status} ·{" "}
                                    {binding.bound_at}
                                  </small>
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className={workspace.safeNote}>
                            此版本尚未绑定到任何机器人。
                          </p>
                        )}
                        {selectedVersion.lifecycle === "PUBLISHED" ? (
                          <div className={workspace.actionRow}>
                            <Input
                              aria-label="机器人区域"
                              value={bindingRegionCode}
                              placeholder="区域代码"
                              onChange={(event) => {
                                setBindingRegionCode(event.target.value);
                                setBindingTarget(null);
                              }}
                            />
                            <Input
                              aria-label="机器人 ID"
                              value={bindingRobotId}
                              placeholder="机器人 ID"
                              onChange={(event) => {
                                setBindingRobotId(event.target.value);
                                setBindingTarget(null);
                              }}
                            />
                            <Button
                              loading={loadRobotBindingTarget.isPending}
                              disabled={
                                !scope?.projectId ||
                                !bindingRegionCode.trim() ||
                                !bindingRobotId.trim()
                              }
                              onClick={() => void loadBindingTarget()}
                            >
                              读取机器人当前版本
                            </Button>
                            {bindingTarget ? (
                              <Button
                                type="primary"
                                loading={bindRobotModelVersion.isPending}
                                onClick={() => void bindRobot()}
                              >
                                绑定 {bindingTarget.displayName}
                              </Button>
                            ) : null}
                          </div>
                        ) : (
                          <p className={workspace.safeNote}>
                            只有已发布版本可以绑定到机器人；草稿请先通过发布预检。
                          </p>
                        )}
                        {bindingError ? (
                          <p className={workspace.warningNote} role="alert">
                            {isDomainError(bindingError)
                              ? bindingError.message
                              : "机器人绑定未完成；请重新读取机器人当前版本后重试。"}
                          </p>
                        ) : null}
                      </section>
                    )
                  ) : null}
                  {tab.id === "validations" ? (
                    <section
                      className={workspace.featureNote}
                      aria-label="发布预检结果"
                    >
                      <strong>发布预检</strong>
                      <p>
                        服务端重新核验资产清单、URDF
                        结构和活动关节映射；结果不会使用浏览器自报的通过状态。
                      </p>
                      {preflight?.result.checks.map((check) => (
                        <p
                          className={
                            check.passed
                              ? workspace.safeNote
                              : workspace.warningNote
                          }
                          key={check.code}
                        >
                          {check.passed ? "通过" : "阻断"} · {check.code}：
                          {check.message}
                        </p>
                      ))}
                      {publishPreflight.error ? (
                        <p className={workspace.warningNote} role="alert">
                          {isDomainError(publishPreflight.error)
                            ? publishPreflight.error.message
                            : "预检未完成；请重试。"}
                        </p>
                      ) : null}
                    </section>
                  ) : null}
                </section>
              ))}
              {incompatibleReason ? (
                <p className={workspace.warningNote} role="alert">
                  {incompatibleReason}
                </p>
              ) : null}
              <div className={workspace.actionRow}>
                {selectedVersion?.lifecycle === "DRAFT" ? (
                  <Button
                    type="primary"
                    loading={publishPreflight.isPending}
                    disabled={mappingState.dirty}
                    onClick={() => void runPublishPreflight()}
                  >
                    运行发布预检
                  </Button>
                ) : null}
                {selectedVersion?.lifecycle === "DRAFT" &&
                preflight?.result.allowed ? (
                  <Button
                    type="primary"
                    loading={publishVersion.isPending}
                    onClick={() => void publish()}
                  >
                    发布固定版本
                  </Button>
                ) : null}
              </div>
              {mappingState.dirty ? (
                <p className={workspace.warningNote} role="status">
                  关节映射有未保存修改；保存后才能运行发布预检。
                </p>
              ) : null}
              {publishVersion.error ? (
                <p className={workspace.warningNote} role="alert">
                  {isDomainError(publishVersion.error)
                    ? publishVersion.error.message
                    : "发布未完成；请重新预检后重试。"}
                </p>
              ) : null}
              {publishMessage ? (
                <p className={workspace.safeNote} role="status">
                  {publishMessage}
                </p>
              ) : null}
            </div>
          </aside>
        </div>
      </StandardPageScaffold>
    </main>
  );
}

export default Component;
