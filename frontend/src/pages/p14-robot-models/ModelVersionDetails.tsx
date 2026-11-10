import { Button, Input, Select } from "antd";
import { useEffect, useState } from "react";
import { createMutationIntentKey } from "../../features/ingest/mutation-machine";
import { useSearchParams } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { formatStorageSize } from "../../shared/lib/metric-presentation";
import { DetailTabs, PageState } from "../../shared/ui";
import workspace from "../../shared/ui/workspace.module.css";
import { robotModelsQueryCodec } from "./query-codec";
import {
  useScopedRobotModelAssetDownload,
  useScopedRobotModelAssets,
  useScopedRobotModelBindings,
  useScopedRobotModelJointMappings,
  useScopedRobotModelVersion,
  useScopedBindRobotModelVersion,
  useScopedLoadRobotBindingTarget,
  useScopedPreflightRobotModelPublish,
  useScopedPublishRobotModelVersion,
  useScopedReplaceRobotModelJointMappings,
  useScopedUploadRobotModelAssets,
} from "../../features/robot-models/api/hooks";
import {
  type RobotModelAssetUploadInput,
  type RobotModelJointMapping,
  type RobotModelPublishPreflight,
} from "../../features/robot-models/api/client";
const detailTabs = [
  { id: "assets", label: "资产文件" },
  { id: "mapping", label: "关节映射" },
  { id: "bindings", label: "绑定机器人" },
  { id: "validations", label: "校验记录" },
] as const;

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

export function ModelVersionDetails({
  organizationId,
  versionId,
}: {
  organizationId: string | null;
  versionId: string;
}) {
  const [params, setParams] = useSearchParams();
  const search = robotModelsQueryCodec.parse(params);
  const activeTab =
    search.detailTab === "overview"
      ? search.targetRobotId
        ? "bindings"
        : "assets"
      : search.detailTab;
  const version = useScopedRobotModelVersion(organizationId, versionId);
  const assets = useScopedRobotModelAssets(
    organizationId,
    version.data?.id ?? null,
  );
  const bindings = useScopedRobotModelBindings(
    organizationId,
    version.data?.id ?? null,
  );
  const jointMappings = useScopedRobotModelJointMappings(
    organizationId,
    version.data?.id ?? null,
  );
  const assetDownload = useScopedRobotModelAssetDownload(organizationId);
  const assetUpload = useScopedUploadRobotModelAssets(organizationId);
  const replaceJointMappings =
    useScopedReplaceRobotModelJointMappings(organizationId);
  const bindRobotModelVersion = useScopedBindRobotModelVersion(organizationId);
  const loadRobotBindingTarget =
    useScopedLoadRobotBindingTarget(organizationId);
  const publishPreflight = useScopedPreflightRobotModelPublish(organizationId);
  const publishVersion = useScopedPublishRobotModelVersion(organizationId);
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
  const [bindingRobotId, setBindingRobotId] = useState(
    search.targetRobotId ?? "",
  );
  const [bindingTarget, setBindingTarget] = useState<{
    readonly robotId: string;
    readonly displayName: string;
    readonly etag: string;
  } | null>(null);

  const selectedVersion =
    !search.modelId || version.data?.robotModelId === search.modelId
      ? version.data
      : undefined;
  const bindingError =
    loadRobotBindingTarget.error ?? bindRobotModelVersion.error;
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
    if (search.targetRobotId) setBindingRobotId(search.targetRobotId);
  }, [search.targetRobotId]);

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
      idempotencyKey: createMutationIntentKey(),
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
      idempotencyKey: createMutationIntentKey(),
    });
    setMappingState({ versionId: updated.id, dirty: false, rows });
    setPreflight(null);
    setPublishMessage("关节映射已保存；可运行发布预检。");
  };

  const runPublishPreflight = async () => {
    if (!selectedVersion || mappingState.dirty) return;
    const idempotencyKey = createMutationIntentKey();
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
    if (!bindingRobotId.trim()) return;
    const target = await loadRobotBindingTarget.mutateAsync({
      robotId: bindingRobotId.trim(),
    });
    setBindingTarget(target);
    setPublishMessage(`已读取机器人 ${target.displayName} 的当前版本标签。`);
  };

  const bindRobot = async () => {
    if (!selectedVersion || !bindingTarget) return;
    const binding = await bindRobotModelVersion.mutateAsync({
      versionId: selectedVersion.id,
      robotId: bindingTarget.robotId,
      robotEtag: bindingTarget.etag,
      idempotencyKey: createMutationIntentKey(),
    });
    setBindingTarget(null);
    setBindingRobotId("");
    setPublishMessage(`已将版本绑定到机器人 ${binding.robot_id}。`);
  };

  if (!selectedVersion)
    return (
      <PageState
        state={version.isPending ? "loading" : "error"}
        label="模型版本"
        onRetry={() => void version.refetch()}
      />
    );
  return (
    <>
      <DetailTabs
        tabs={detailTabs}
        activeTab={activeTab}
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
          hidden={activeTab !== tab.id}
        >
          {tab.id === "assets" ? (
            assets.isPending ? (
              <PageState state="loading" label="固定版本资产" />
            ) : assets.error ? (
              <PageState state="error" onRetry={() => void assets.refetch()} />
            ) : !selectedVersion ? (
              <PageState state="empty" title="选择固定版本后查看资产" />
            ) : assets.data?.length ? (
              <ul className={workspace.factList} aria-label="固定版本资产清单">
                {assets.data.map((asset) => (
                  <li className={workspace.factRow} key={asset.asset_id}>
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
                        assetDownload.variables?.assetId === asset.asset_id
                      }
                      onClick={() => void downloadAsset(asset.asset_id)}
                    >
                      下载
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <PageState state="empty" title="这个固定版本还没有完成的资产" />
            )
          ) : null}
          {tab.id === "assets" && selectedVersion?.lifecycle === "DRAFT" ? (
            <section
              className={workspace.featureNote}
              aria-label="上传固定版本资产"
            >
              <strong>上传资产</strong>
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
                    setSelectedAssetFile(event.target.files?.item(0) ?? null)
                  }
                />
              </label>
              {selectedAssetFile && !assetMetadata(selectedAssetFile) ? (
                <p className={workspace.warningNote} role="alert">
                  该扩展名不属于受支持的 URDF、网格、纹理、配置或文档资产。
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
            ) : (
              <section
                className={workspace.mappingPanel}
                aria-label={
                  selectedVersion.lifecycle === "DRAFT"
                    ? "关节映射编辑器"
                    : "关节映射详情"
                }
              >
                <header className={workspace.mappingPanelHeader}>
                  <div>
                    <strong>关节映射</strong>
                    <p>
                      源关节对应采集数据字段，目标关节对应 URDF 的活动关节。
                    </p>
                  </div>
                  <span className={workspace.inlineMeta}>
                    {selectedVersion.lifecycle === "DRAFT"
                      ? mappingState.rows.length
                      : (jointMappings.data?.length ?? 0)}{" "}
                    项
                  </span>
                </header>
                {selectedVersion.lifecycle !== "DRAFT" ? (
                  <p className={workspace.safeNote}>
                    已发布版本的关节映射仅可查看；如需修改，请创建新的草稿版本。
                  </p>
                ) : null}
                {selectedVersion.lifecycle === "DRAFT" ? (
                  <>
                    <div className={workspace.mappingEditor}>
                      {mappingState.rows.map((mapping, index) => (
                        <div
                          className={workspace.mappingEditRow}
                          key={`${index}-${mapping.source_joint_name}`}
                        >
                          <Input
                            aria-label={`源关节 ${index + 1}`}
                            value={mapping.source_joint_name}
                            placeholder="数据源关节"
                            onChange={(event) =>
                              updateMappingRow(index, {
                                source_joint_name: event.target.value,
                              })
                            }
                          />
                          <Input
                            aria-label={`目标关节 ${index + 1}`}
                            value={mapping.target_joint_name}
                            placeholder="URDF 目标关节"
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
                    </div>
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
                  </>
                ) : jointMappings.data?.length ? (
                  <div className={workspace.mappingTableWrap}>
                    <table className={workspace.definitionTable}>
                      <caption className={workspace.srOnly}>
                        固定版本关节映射
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">数据源关节</th>
                          <th scope="col">URDF 目标关节</th>
                          <th scope="col">方向</th>
                        </tr>
                      </thead>
                      <tbody>
                        {jointMappings.data.map((mapping, index) => (
                          <tr key={`${index}-${mapping.source_joint_name}`}>
                            <td>
                              <code>{mapping.source_joint_name}</code>
                            </td>
                            <td>
                              <code>{mapping.target_joint_name}</code>
                            </td>
                            <td>
                              {mapping.direction === "SAME" ? "同向" : "反向"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <PageState
                    state="empty"
                    title="该固定版本没有关节映射"
                    description="该版本仍可查看模型资产，但没有可用于关节数据播放的映射。"
                  />
                )}
              </section>
            )
          ) : null}
          {tab.id === "bindings" ? (
            !selectedVersion ? (
              <PageState
                state="empty"
                title="选择固定版本后查看绑定"
                description="每条绑定都指向组织内唯一的机器人和已发布模型版本。"
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
                  <ul className={workspace.factList} aria-label="绑定历史">
                    {bindings.data.map((binding) => (
                      <li
                        className={workspace.factRow}
                        key={binding.binding_id}
                      >
                        <span>
                          <strong>{binding.robot_id}</strong>
                          <small>
                            {binding.status} · {binding.bound_at}
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
                      disabled={!bindingRobotId.trim()}
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
              {preflight?.result.checks.map((check) => (
                <p
                  className={
                    check.passed ? workspace.safeNote : workspace.warningNote
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
        {selectedVersion?.lifecycle === "DRAFT" && preflight?.result.allowed ? (
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
    </>
  );
}
