import { Alert, Button, Form, Input, Modal, Select, Spin, Steps } from "antd";
import {
  Box,
  CheckCircle2,
  CircleDot,
  File as FileIcon,
  FolderOpen,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RobotModel, RobotModelVersion } from "../../entities/robot-model";
import {
  authorizeRobotModelAssetDownload,
  authorizeRobotModelViewerAssets,
  discardRobotModelImport,
  getRobotModelVersion,
  useCreateRobotModel,
  useCreateRobotModelDraft,
  usePreflightRobotModelPublish,
  usePublishRobotModelVersion,
  useReplaceRobotModelJointMappings,
  useRobotModelAssets,
  useRobotModelJointMappings,
  useRobotModels,
  useRobotModelVersion,
  useUploadRobotModelAssets,
  type RobotModelJointMapping,
} from "../../features/robot-models/api";
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
import workspace from "../ui-011e/workspace.module.css";
import {
  classifyRobotModelFiles,
  modelAssetSelectionError,
  robotModelFilesFromDrop,
  type PendingRobotModelAsset,
  type RobotModelFileCandidate,
} from "./model-file-selection";
import uploadStyles from "./model-upload.module.css";
import { robotsQueryCodec } from "./query-codec";
import styles from "./robot-assets.module.css";
import {
  buildRobotConfigurationAsset,
  createLocalRobotPreview,
  mappingsCoverUrdf,
  parseRobotModelAssets,
  replaceRobotConfigurationAsset,
  type LocalRobotPreview,
  type ParsedRobotModel,
} from "./urdf-import";

type ImportStage = "files" | "review" | "saving" | "saved";

interface ModelMetadataFormValues {
  readonly manufacturer?: string;
  readonly modelCode?: string;
  readonly displayName?: string;
  readonly versionLabel: string;
}

const EMPTY_MAPPING: Readonly<Record<string, string>> = {};

const modelFileAccept = [
  ".urdf",
  ".xml",
  ".stl",
  ".obj",
  ".dae",
  ".glb",
  ".gltf",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".ktx2",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
].join(",");

const modelAssetRoleLabels: Record<PendingRobotModelAsset["role"], string> = {
  URDF: "URDF",
  MESH: "网格",
  TEXTURE: "纹理",
  CONFIG: "配置",
  DOCUMENTATION: "文档",
};

const parseErrorLabels: Readonly<Record<string, string>> = {
  URDF_MISSING: "没有找到 URDF 文件，请选择一个 .urdf 或 .xml 文件。",
  URDF_MULTIPLE: "检测到多个 URDF，请只保留一个入口 URDF。",
  URDF_XML_INVALID: "URDF 不是有效 XML，或根节点不是 <robot>。",
  URDF_ROBOT_NAME_MISSING: "URDF 的 <robot> 缺少 name。",
  URDF_LINKS_MISSING: "URDF 中没有 link，无法构成机器人模型。",
  URDF_LINKS_DUPLICATED: "URDF 中存在重复 link 名称。",
  URDF_JOINT_NAME_MISSING: "URDF 中存在没有 name 的 joint。",
  URDF_JOINTS_DUPLICATED: "URDF 中存在重复 joint 名称。",
  CONFIG_JSON_OBJECT_REQUIRED: "JSON 配置文件的根节点必须是对象。",
};

async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function uniqueVersionLabel(prefix: string): string {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:TZ.]/gu, "")
    .slice(0, 14);
  return `${prefix}-${stamp}`;
}

function importStep(stage: ImportStage): number {
  if (stage === "files") return 0;
  if (stage === "review") return 1;
  return 2;
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
  const search = robotsQueryCodec.parse(params);
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

  const [query, setQuery] = useState(search.q ?? "");
  const [modelForm] = Form.useForm<ModelMetadataFormValues>();

  const models = useRobotModels({
    ...(search.q ? { q: search.q } : {}),
    ...(search.after ? { after: search.after } : {}),
    ...(search.before ? { before: search.before } : {}),
    limit: search.limit,
  });
  const modelItems = (models.data?.items ?? []).filter(
    (model) => model.currentPublishedVersionId,
  );
  const selectedModel =
    modelItems.find((model) => model.id === search.modelId) ?? modelItems[0];
  const selectedVersionId = selectedModel?.currentPublishedVersionId ?? null;
  const currentVersion = useRobotModelVersion(selectedVersionId);
  const currentAssets = useRobotModelAssets(selectedVersionId);
  const currentMappings = useRobotModelJointMappings(selectedVersionId);
  const currentUrdf = currentAssets.data?.find(
    (asset) => asset.role === "URDF",
  );

  const createRobotModel = useCreateRobotModel();
  const createModelDraft = useCreateRobotModelDraft();
  const uploadModelAssets = useUploadRobotModelAssets();
  const replaceModelMappings = useReplaceRobotModelJointMappings();
  const publishPreflight = usePreflightRobotModelPublish();
  const publishVersion = usePublishRobotModelVersion();

  const [importOpen, setImportOpen] = useState(false);
  const [importStage, setImportStage] = useState<ImportStage>("files");
  const [importModel, setImportModel] = useState<RobotModel | null>(null);
  const [importSourceVersion, setImportSourceVersion] =
    useState<RobotModelVersion | null>(null);
  const [modelMetadata, setModelMetadata] =
    useState<ModelMetadataFormValues | null>(null);
  const [modelAssets, setModelAssets] = useState<
    readonly PendingRobotModelAsset[]
  >([]);
  const [analysis, setAnalysis] = useState<ParsedRobotModel | null>(null);
  const [mappings, setMappings] = useState<readonly RobotModelJointMapping[]>(
    [],
  );
  const [localPreview, setLocalPreview] = useState<LocalRobotPreview | null>(
    null,
  );
  const localPreviewRef = useRef<LocalRobotPreview | null>(null);
  const previewMountGenerationRef = useRef(0);
  const [configurationPreview, setConfigurationPreview] = useState("");
  const [modelCommandId, setModelCommandId] = useState("");
  const [modelDropActive, setModelDropActive] = useState(false);
  const [readingDroppedFiles, setReadingDroppedFiles] = useState(false);
  const [loadingExistingModel, setLoadingExistingModel] = useState(false);
  const [parsingModel, setParsingModel] = useState(false);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [savedVersionId, setSavedVersionId] = useState<string | null>(null);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const modelFileInputRef = useRef<HTMLInputElement>(null);
  const modelFolderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const generation = ++previewMountGenerationRef.current;
    return () => {
      // React StrictMode intentionally runs an immediate setup/cleanup/setup
      // cycle. Defer revocation and keep URLs alive when that second setup
      // takes ownership of the same local preview.
      queueMicrotask(() => {
        if (previewMountGenerationRef.current !== generation) return;
        localPreviewRef.current?.dispose();
        localPreviewRef.current = null;
      });
    };
  }, []);

  const modelSelectionError = modelAssetSelectionError(modelAssets);
  const modelAssetCounts = useMemo(
    () =>
      modelAssets.reduce(
        (counts, asset) => ({
          ...counts,
          [asset.role]: counts[asset.role] + 1,
        }),
        {
          URDF: 0,
          MESH: 0,
          TEXTURE: 0,
          CONFIG: 0,
          DOCUMENTATION: 0,
        } satisfies Record<PendingRobotModelAsset["role"], number>,
      ),
    [modelAssets],
  );
  const mappingsValid = Boolean(
    analysis && mappingsCoverUrdf(mappings, analysis.actuatedJointNames),
  );
  const modelSavePending =
    importStage === "saving" ||
    createRobotModel.isPending ||
    createModelDraft.isPending ||
    uploadModelAssets.isPending ||
    replaceModelMappings.isPending ||
    publishPreflight.isPending ||
    publishVersion.isPending;

  useEffect(() => {
    if (!selectedModel || search.modelId === selectedModel.id) return;
    setParams(
      robotsQueryCodec.build(
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
  ]);

  const localModelRef = useMemo(
    () => ({
      modelId: importModel?.id ?? modelMetadata?.modelCode ?? "local-model",
      modelVersion: modelCommandId || "local-preview",
    }),
    [importModel?.id, modelCommandId, modelMetadata?.modelCode],
  );
  const localRuntimeLoader = useMemo(() => {
    if (!localPreview) return;
    return createLazyThreeRobotSceneLoader(async () => ({
      manifest: {
        modelId: localModelRef.modelId,
        modelVersion: localModelRef.modelVersion,
        requiredJoints: [],
      },
      urdfUrl: localPreview.urdfUrl,
      background: "#f5f7fc",
    }));
  }, [localModelRef, localPreview]);

  useEffect(() => {
    if (!analysis || !modelMetadata) {
      setConfigurationPreview("");
      return;
    }
    const asset = buildRobotConfigurationAsset(analysis, mappings, {
      ...(importModel ? { modelId: importModel.id } : {}),
      displayName:
        modelMetadata.displayName ??
        importModel?.displayName ??
        analysis.robotName,
      manufacturer:
        modelMetadata.manufacturer ?? importModel?.manufacturer ?? "",
      modelCode:
        modelMetadata.modelCode ?? importModel?.modelCode ?? analysis.robotName,
    });
    let active = true;
    void asset.file.text().then((content) => {
      if (active) setConfigurationPreview(content);
    });
    return () => {
      active = false;
    };
  }, [analysis, importModel, mappings, modelMetadata]);

  const selectModel = (modelId: string) => {
    setParams(
      robotsQueryCodec.build(
        {
          ...search,
          modelId,
        },
        search,
      ),
    );
  };

  const replaceLocalPreview = (preview: LocalRobotPreview | null) => {
    if (localPreviewRef.current !== preview) localPreviewRef.current?.dispose();
    localPreviewRef.current = preview;
    setLocalPreview(preview);
  };

  const clearAnalysis = () => {
    setAnalysis(null);
    setMappings([]);
    replaceLocalPreview(null);
    setConfigurationPreview("");
    setImportStage("files");
    setSavedVersionId(null);
  };

  const resetImport = () => {
    clearAnalysis();
    setImportModel(null);
    setImportSourceVersion(null);
    setModelMetadata(null);
    setModelAssets([]);
    setModelNotice(null);
    setModelError(null);
    setModelDropActive(false);
    setReadingDroppedFiles(false);
    setLoadingExistingModel(false);
    setParsingModel(false);
    setModelCommandId("");
    modelForm.resetFields();
  };

  const defaultModelMetadata = (
    model: RobotModel | null,
    sourceVersion: RobotModelVersion | null,
  ): ModelMetadataFormValues => ({
    manufacturer: model?.manufacturer ?? "",
    modelCode: model?.modelCode ?? "",
    displayName: model?.displayName ?? "",
    versionLabel: uniqueVersionLabel(sourceVersion ? "update" : "1.0.0"),
  });

  const beginImport = (
    model: RobotModel | null,
    sourceVersion: RobotModelVersion | null,
  ) => {
    resetImport();
    const metadata = defaultModelMetadata(model, sourceVersion);
    setImportModel(model);
    setImportSourceVersion(sourceVersion);
    setModelMetadata(metadata);
    setModelCommandId(crypto.randomUUID());
    modelForm.setFieldsValue(metadata);
    setImportOpen(true);
  };

  const retryCleanup = async (cleanup: () => Promise<void>) => {
    try {
      await cleanup();
      return true;
    } catch {
      try {
        await cleanup();
        return true;
      } catch {
        return false;
      }
    }
  };

  const rollbackImport = async (
    versionId: string | null,
    sourceVersion: RobotModelVersion | null,
  ) => {
    const results: boolean[] = [];
    if (versionId && organizationId) {
      results.push(
        await retryCleanup(() =>
          discardRobotModelImport(organizationId, versionId, sourceVersion?.id),
        ),
      );
    }
    await models.refetch();
    return results.every(Boolean);
  };

  const cancelImport = async () => {
    if (modelSavePending) return;
    setImportOpen(false);
    resetImport();
  };

  const analyseSelectedFiles = async (
    assets: readonly PendingRobotModelAsset[] = modelAssets,
    preferredMappings: readonly RobotModelJointMapping[] = [],
  ) => {
    setParsingModel(true);
    setModelError(null);
    try {
      const parsed = await parseRobotModelAssets(assets);
      const nextMappings = mappingsCoverUrdf(
        preferredMappings,
        parsed.actuatedJointNames,
      )
        ? preferredMappings
        : parsed.mappings;
      const preview = createLocalRobotPreview(parsed, assets);
      setAnalysis(parsed);
      setMappings(nextMappings);
      replaceLocalPreview(preview);
      setImportStage("review");
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      setModelError(
        parseErrorLabels[code] ??
          (error instanceof SyntaxError
            ? "JSON 配置文件格式错误，请修正后重新选择。"
            : "模型文件解析失败，请检查 URDF 与配置文件。"),
      );
    } finally {
      setParsingModel(false);
    }
  };

  const reviewSelectedFiles = async () => {
    try {
      const metadata = await modelForm.validateFields();
      setModelMetadata(metadata);
      await analyseSelectedFiles();
    } catch {
      setModelError("请先补全模型名称和版本信息。");
    }
  };

  const editCurrentModel = async () => {
    if (
      !selectedModel ||
      !currentVersion.data ||
      !currentAssets.data ||
      !organizationId
    ) {
      return;
    }
    beginImport(selectedModel, currentVersion.data);
    setLoadingExistingModel(true);
    try {
      const downloaded = await Promise.all(
        currentAssets.data.map(async (asset) => {
          const authorization = await authorizeRobotModelAssetDownload(
            organizationId,
            currentVersion.data!.id,
            asset.asset_id,
          );
          const response = await fetch(authorization.download_url, {
            cache: "no-store",
            credentials: "omit",
          });
          if (!response.ok) throw new Error("ASSET_DOWNLOAD_FAILED");
          const blob = await response.blob();
          return {
            file: new File([blob], asset.relative_path.split("/").at(-1)!, {
              type: asset.media_type,
            }),
            relativePath: asset.relative_path,
          } satisfies RobotModelFileCandidate;
        }),
      );
      const classified = classifyRobotModelFiles(downloaded);
      setModelAssets(classified.assets);
      await analyseSelectedFiles(classified.assets, currentMappings.data ?? []);
    } catch (error) {
      setModelError(
        isDomainError(error)
          ? error.message
          : "当前模型文件读取失败，请稍后重试。",
      );
    } finally {
      setLoadingExistingModel(false);
    }
  };

  const addModelFileCandidates = (
    candidates: readonly RobotModelFileCandidate[],
  ) => {
    const incoming = classifyRobotModelFiles(candidates);
    setModelAssets((current) => {
      const replacesUrdf = incoming.assets.some(
        (asset) => asset.role === "URDF",
      );
      const replacesJsonConfig = incoming.assets.some(
        (asset) =>
          asset.role === "CONFIG" &&
          asset.relativePath.toLowerCase().endsWith(".json"),
      );
      const retained = current.filter(
        (asset) =>
          !(replacesUrdf && asset.role === "URDF") &&
          !(
            replacesJsonConfig &&
            asset.role === "CONFIG" &&
            asset.relativePath.toLowerCase().endsWith(".json")
          ),
      );
      return classifyRobotModelFiles([
        ...retained.map((asset) => ({
          file: asset.file,
          relativePath: asset.relativePath,
        })),
        ...incoming.assets.map((asset) => ({
          file: asset.file,
          relativePath: asset.relativePath,
        })),
      ]).assets;
    });
    clearAnalysis();
    setModelNotice(
      incoming.rejectedPaths.length
        ? `已忽略 ${incoming.rejectedPaths.length} 个不支持、空文件或路径不安全的文件。`
        : null,
    );
    setModelError(null);
  };

  const addModelFilesFromPicker = (files: FileList | null) => {
    if (!files) return;
    addModelFileCandidates(
      Array.from(files).map((file) => ({
        file,
        relativePath: file.webkitRelativePath || file.name,
      })),
    );
  };

  const handleModelFilesDrop = async (dataTransfer: DataTransfer) => {
    setModelDropActive(false);
    setReadingDroppedFiles(true);
    setModelError(null);
    try {
      const candidates = await robotModelFilesFromDrop(dataTransfer);
      addModelFileCandidates(candidates);
      if (!candidates.length) {
        setModelNotice("没有读取到文件，请改用“选择文件夹”。");
      }
    } catch {
      setModelError("无法读取拖入的文件夹，请改用“选择文件夹”重试。");
    } finally {
      setReadingDroppedFiles(false);
    }
  };

  const saveRobotModel = async () => {
    if (
      !analysis ||
      !organizationId ||
      !modelCommandId ||
      !modelMetadata ||
      !mappingsValid
    ) {
      return;
    }
    const values = modelMetadata;
    const sourceVersionToRestore = importSourceVersion;
    let draftVersionId: string | null = null;
    setImportStage("saving");
    setModelError(null);
    try {
      const configurationAsset = buildRobotConfigurationAsset(
        analysis,
        mappings,
        {
          ...(importModel ? { modelId: importModel.id } : {}),
          displayName:
            values.displayName ??
            importModel?.displayName ??
            analysis.robotName,
          manufacturer: values.manufacturer ?? importModel?.manufacturer ?? "",
          modelCode:
            values.modelCode ?? importModel?.modelCode ?? analysis.robotName,
        },
      );
      const assetsToSave = replaceRobotConfigurationAsset(
        modelAssets,
        configurationAsset,
      );
      const draft = importSourceVersion
        ? await createModelDraft.mutateAsync({
            sourceVersionId: importSourceVersion.id,
            versionLabel: values.versionLabel.trim(),
            updateScope: "ASSETS",
            idempotencyKey: modelCommandId,
          })
        : await createRobotModel.mutateAsync({
            manufacturer: values.manufacturer?.trim() ?? "",
            modelCode: values.modelCode?.trim() ?? "",
            displayName: values.displayName?.trim() ?? "",
            versionLabel: values.versionLabel.trim(),
            idempotencyKey: modelCommandId,
          });
      draftVersionId = draft.id;
      const files: Array<PendingRobotModelAsset & { readonly sha256: string }> =
        [];
      for (const asset of assetsToSave) {
        files.push({ ...asset, sha256: await sha256File(asset.file) });
      }
      await uploadModelAssets.mutateAsync({
        versionId: draft.id,
        files,
        idempotencyKey: `${modelCommandId}:assets`,
      });
      const uploadedDraft = await getRobotModelVersion(
        organizationId,
        draft.id,
      );
      const configuredDraft = await replaceModelMappings.mutateAsync({
        versionId: draft.id,
        etag: uploadedDraft.etag,
        mappings,
        idempotencyKey: `${modelCommandId}:mappings`,
      });
      const preflight = await publishPreflight.mutateAsync({
        versionId: draft.id,
        etag: configuredDraft.etag,
        // The publish token is bound to the command identity that consumes it.
        // Reusing the same key across preflight and publish is therefore part of
        // the backend contract, not an accidental retry collision.
        idempotencyKey: `${modelCommandId}:publish`,
      });
      if (!preflight.allowed || !preflight.preflight_token) {
        const failed = preflight.checks.filter((check) => !check.passed);
        throw new Error(
          failed.length
            ? failed.map((check) => check.message).join("；")
            : "服务端校验未通过。",
        );
      }
      const published = await publishVersion.mutateAsync({
        versionId: draft.id,
        etag: preflight.expected_etag,
        preflightToken: preflight.preflight_token,
        idempotencyKey: `${modelCommandId}:publish`,
      });
      setSavedVersionId(published.id);
      setImportStage("saved");
      await models.refetch();
      selectModel(published.robotModelId);
    } catch (error) {
      const failureMessage = isDomainError(error)
        ? error.message
        : error instanceof Error && error.message
          ? `保存失败：${error.message}`
          : "模型保存失败，请检查文件后重试。";
      const cleaned = await rollbackImport(
        draftVersionId,
        sourceVersionToRestore,
      );
      setImportOpen(false);
      resetImport();
      setPageNotice(
        cleaned
          ? `${failureMessage} 未完成的模型版本和服务器临时文件已自动清理，请重新添加。`
          : `${failureMessage} 自动清理未能完全确认；系统已阻止失败项显示，请联系管理员检查服务器存储。`,
      );
    }
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

  const configurationAsset =
    analysis && modelMetadata
      ? buildRobotConfigurationAsset(analysis, mappings, {
          ...(importModel ? { modelId: importModel.id } : {}),
          displayName:
            modelMetadata.displayName ??
            importModel?.displayName ??
            analysis.robotName,
          manufacturer:
            modelMetadata.manufacturer ?? importModel?.manufacturer ?? "",
          modelCode:
            modelMetadata.modelCode ??
            importModel?.modelCode ??
            analysis.robotName,
        })
      : null;

  return (
    <main className={workspace.page} data-page-id="P15">
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
              value={String(modelItems.length)}
            />
            <SummaryItem
              icon={<FileIcon aria-hidden="true" size={20} />}
              label="当前版本"
              value={currentVersion.data?.versionLabel ?? "—"}
            />
            <SummaryItem
              icon={<CheckCircle2 aria-hidden="true" size={20} />}
              label="发布状态"
              value={modelItems.length ? "可用" : "—"}
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
                robotsQueryCodec.build(
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
                robotsQueryCodec.build(
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
                <h2>
                  {search.q ? "没有匹配的模型" : "还没有已发布的模型资产"}
                </h2>
                <p>
                  {search.q
                    ? "请调整搜索条件后重试。"
                    : "导入文件并通过解析、校验和发布后，模型才会显示在这里。失败或取消的草稿不会保留。"}
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
                    <p>仅显示已成功发布的模型资产</p>
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
                        status="PUBLISHED"
                        label="已发布"
                        tone="success"
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
                          robotsQueryCodec.build(
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
                  <StatusTag status="PUBLISHED" label="已发布" tone="success" />
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
                    </div>
                  </aside>
                </div>
              </section>
            </div>
          )}
        </div>
      </StandardPageScaffold>

      <Modal
        open={importOpen}
        width={1040}
        title={importSourceVersion ? "检查并修改机器人模型" : "导入机器人模型"}
        footer={null}
        destroyOnHidden
        mask={{ closable: !modelSavePending }}
        onCancel={() => void cancelImport()}
      >
        <Steps
          className={styles.wizardSteps}
          current={importStep(importStage)}
          items={[
            { title: "导入文件", content: "URDF + 描述配置" },
            { title: "解析与预览", content: "检查结构和映射" },
            { title: "保存", content: "校验并发布" },
          ]}
        />

        {loadingExistingModel ? (
          <div className={styles.savingState} role="status">
            <div className={styles.savingCopy}>
              <Spin size="large" />
              <h3>正在读取当前模型文件…</h3>
              <p>文件读取完成后会自动解析，并进入可视化检查。</p>
            </div>
          </div>
        ) : importStage === "files" ? (
          <div className={styles.wizardSection}>
            <Alert
              type="info"
              showIcon
              title="先导入，解析成功后才能预览和保存"
              description="必须包含一个入口 URDF。描述配置使用 JSON；如果没有提供，系统会根据 URDF 自动生成 robot.config.json。"
            />
            <Form<ModelMetadataFormValues>
              form={modelForm}
              layout="vertical"
              requiredMark="optional"
            >
              {!importSourceVersion ? (
                <div className={styles.wizardMeta}>
                  <Form.Item
                    name="manufacturer"
                    label="制造商"
                    rules={[{ required: true, whitespace: true, max: 256 }]}
                  >
                    <Input autoComplete="organization" maxLength={256} />
                  </Form.Item>
                  <Form.Item
                    name="modelCode"
                    label="型号代码"
                    rules={[{ required: true, whitespace: true, max: 128 }]}
                  >
                    <Input autoComplete="off" maxLength={128} />
                  </Form.Item>
                  <Form.Item
                    name="displayName"
                    label="模型名称"
                    rules={[{ required: true, whitespace: true, max: 256 }]}
                  >
                    <Input autoComplete="off" maxLength={256} />
                  </Form.Item>
                </div>
              ) : null}
              <Form.Item
                name="versionLabel"
                label="保存版本"
                rules={[{ required: true, whitespace: true, max: 128 }]}
              >
                <Input autoComplete="off" maxLength={128} />
              </Form.Item>
            </Form>
            <div
              className={`${uploadStyles.dropZone} ${modelDropActive ? uploadStyles.dropZoneActive : ""}`}
              role="button"
              tabIndex={0}
              aria-label="拖拽或选择 URDF 和机器人配置文件"
              aria-busy={readingDroppedFiles}
              onClick={(event) => {
                const target = event.target;
                if (
                  target instanceof Element &&
                  target.closest("button, input")
                )
                  return;
                modelFileInputRef.current?.click();
              }}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  modelFileInputRef.current?.click();
                }
              }}
              onDragEnter={(event) => {
                event.preventDefault();
                setModelDropActive(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
                setModelDropActive(true);
              }}
              onDragLeave={(event) => {
                if (
                  !event.currentTarget.contains(
                    event.relatedTarget as Node | null,
                  )
                ) {
                  setModelDropActive(false);
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                event.stopPropagation();
                void handleModelFilesDrop(event.dataTransfer);
              }}
            >
              <div className={uploadStyles.dropZoneContent}>
                <span className={uploadStyles.dropZoneIcon}>
                  <UploadCloud aria-hidden="true" size={24} />
                </span>
                <strong>
                  {readingDroppedFiles
                    ? "正在读取文件夹…"
                    : "拖拽 URDF、配置文件或整个模型文件夹"}
                </strong>
                <span className={uploadStyles.dropZoneCopy}>
                  选择新的 URDF 或 JSON
                  配置时会替换同类型旧文件；网格和纹理会保留目录结构。
                </span>
                <div className={uploadStyles.uploadActions}>
                  <Button
                    icon={<FileIcon aria-hidden="true" size={16} />}
                    disabled={readingDroppedFiles}
                    onClick={(event) => {
                      event.stopPropagation();
                      modelFileInputRef.current?.click();
                    }}
                  >
                    选择文件
                  </Button>
                  <Button
                    icon={<FolderOpen aria-hidden="true" size={16} />}
                    disabled={readingDroppedFiles}
                    onClick={(event) => {
                      event.stopPropagation();
                      modelFolderInputRef.current?.click();
                    }}
                  >
                    选择文件夹
                  </Button>
                </div>
              </div>
              <input
                ref={modelFileInputRef}
                className={uploadStyles.hiddenInput}
                aria-label="选择机器人模型文件"
                type="file"
                multiple
                accept={modelFileAccept}
                onChange={(event) => {
                  addModelFilesFromPicker(event.target.files);
                  event.target.value = "";
                }}
              />
              <input
                ref={(element) => {
                  modelFolderInputRef.current = element;
                  element?.setAttribute("webkitdirectory", "");
                  element?.setAttribute("directory", "");
                }}
                className={uploadStyles.hiddenInput}
                aria-label="选择机器人模型文件夹"
                type="file"
                multiple
                accept={modelFileAccept}
                onChange={(event) => {
                  addModelFilesFromPicker(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>

            {modelAssets.length ? (
              <>
                <div
                  className={uploadStyles.selectionSummary}
                  aria-live="polite"
                >
                  <strong>已选择 {modelAssets.length} 个文件</strong>
                  <span>URDF {modelAssetCounts.URDF}</span>
                  <span>网格 {modelAssetCounts.MESH}</span>
                  <span>纹理 {modelAssetCounts.TEXTURE}</span>
                  <span>配置 {modelAssetCounts.CONFIG}</span>
                </div>
                <ul
                  className={uploadStyles.fileList}
                  aria-label="待解析模型文件"
                >
                  {modelAssets.map((asset) => (
                    <li
                      className={uploadStyles.fileRow}
                      key={asset.relativePath}
                    >
                      <span
                        className={uploadStyles.filePath}
                        title={asset.relativePath}
                      >
                        {asset.relativePath}
                      </span>
                      <span className={uploadStyles.fileRole}>
                        {modelAssetRoleLabels[asset.role]}
                      </span>
                      <button
                        className={uploadStyles.removeButton}
                        type="button"
                        aria-label={`移除 ${asset.relativePath}`}
                        onClick={() => {
                          setModelAssets((current) =>
                            current.filter(
                              (item) =>
                                item.relativePath !== asset.relativePath,
                            ),
                          );
                          clearAnalysis();
                          setModelError(null);
                        }}
                      >
                        <X aria-hidden="true" size={15} />
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {modelNotice ? (
              <Alert type="warning" showIcon title={modelNotice} />
            ) : null}
            {modelError ? (
              <Alert type="error" showIcon title={modelError} />
            ) : null}
            <div className={styles.wizardFooter}>
              <Button onClick={() => void cancelImport()}>取消</Button>
              <Button
                type="primary"
                loading={parsingModel}
                disabled={!modelAssets.length || Boolean(modelSelectionError)}
                onClick={() => void reviewSelectedFiles()}
              >
                解析文件并预览
              </Button>
            </div>
          </div>
        ) : importStage === "review" && analysis && modelMetadata ? (
          <div className={styles.wizardSection}>
            <div className={styles.wizardMeta}>
              <div>
                <span>入口 URDF</span>
                <strong title={analysis.urdfPath}>{analysis.urdfPath}</strong>
              </div>
              <div>
                <span>URDF 模型名称</span>
                <strong>{analysis.robotName}</strong>
              </div>
              <div>
                <span>描述配置</span>
                <strong>{configurationAsset?.relativePath}</strong>
              </div>
              <div>
                <span>Link</span>
                <strong>{analysis.linkNames.length}</strong>
              </div>
              <div>
                <span>Joint</span>
                <strong>{analysis.joints.length}</strong>
              </div>
              <div>
                <span>活动关节</span>
                <strong>{analysis.actuatedJointNames.length}</strong>
              </div>
            </div>

            {analysis.missingMeshReferences.length ? (
              <Alert
                type="warning"
                showIcon
                title="URDF 引用的部分资源未找到"
                description={analysis.missingMeshReferences.join("、")}
              />
            ) : (
              <Alert
                type="success"
                showIcon
                title="URDF 结构解析通过"
                description="请旋转、缩放 3D 模型检查方向和结构，再确认关节映射。"
              />
            )}

            <div className={styles.reviewGrid}>
              <div className={styles.wizardPreview}>
                {localRuntimeLoader ? (
                  <RobotSceneCore
                    modelRef={localModelRef}
                    jointMapping={EMPTY_MAPPING}
                    runtimeLoader={localRuntimeLoader}
                  />
                ) : (
                  <div className={styles.emptyPreview}>3D 预览正在准备…</div>
                )}
              </div>
              <section
                className={styles.mappingPanel}
                aria-label="关节映射配置"
              >
                <header className={styles.mappingHeader}>
                  <div>
                    <h3>关节映射</h3>
                    <p>修改后会直接写入 {configurationAsset?.relativePath}</p>
                  </div>
                  <StatusTag
                    status={mappingsValid ? "VALID" : "INVALID"}
                    label={mappingsValid ? "完整" : "需修正"}
                    tone={mappingsValid ? "success" : "warning"}
                  />
                </header>
                {mappings.length ? (
                  <ul className={styles.mappingList}>
                    {mappings.map((mapping, index) => (
                      <li
                        className={styles.mappingRow}
                        key={`${mapping.target_joint_name}-${index}`}
                      >
                        <Input
                          aria-label={`${mapping.target_joint_name} 的数据关节名`}
                          value={mapping.source_joint_name}
                          placeholder="数据关节名"
                          onChange={(event) =>
                            setMappings((current) =>
                              current.map((item, itemIndex) =>
                                itemIndex === index
                                  ? {
                                      ...item,
                                      source_joint_name: event.target.value,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                        <span
                          className={styles.mappingArrow}
                          aria-hidden="true"
                        >
                          →
                        </span>
                        <Select
                          aria-label={`${mapping.source_joint_name} 对应的 URDF 关节`}
                          value={mapping.target_joint_name}
                          options={analysis.actuatedJointNames.map(
                            (jointName) => ({
                              value: jointName,
                              label: jointName,
                            }),
                          )}
                          onChange={(targetJointName) =>
                            setMappings((current) =>
                              current.map((item, itemIndex) =>
                                itemIndex === index
                                  ? {
                                      ...item,
                                      target_joint_name: targetJointName,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                        <Select
                          aria-label={`${mapping.source_joint_name} 的方向`}
                          value={mapping.direction}
                          options={[
                            { value: "SAME", label: "同向" },
                            { value: "INVERTED", label: "反向" },
                          ]}
                          onChange={(direction: "SAME" | "INVERTED") =>
                            setMappings((current) =>
                              current.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, direction }
                                  : item,
                              ),
                            )
                          }
                        />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={workspace.safeNote}>该 URDF 没有活动关节。</p>
                )}
                <details>
                  <summary>查看将要保存的描述配置</summary>
                  <pre className={styles.configurationPreview}>
                    {configurationPreview}
                  </pre>
                </details>
              </section>
            </div>
            {modelError ? (
              <Alert type="error" showIcon title={modelError} />
            ) : null}
            <div className={styles.wizardFooter}>
              <Button onClick={() => clearAnalysis()}>返回重新选择</Button>
              <Button
                type="primary"
                disabled={
                  !mappingsValid ||
                  Boolean(analysis.missingMeshReferences.length)
                }
                onClick={() => void saveRobotModel()}
              >
                保存机器人模型
              </Button>
            </div>
          </div>
        ) : importStage === "saving" ? (
          <div className={styles.savingState} role="status">
            <div className={styles.savingCopy}>
              <Spin size="large" />
              <h3>正在保存机器人模型…</h3>
              <p>
                正在写入 URDF、描述配置和关节映射，并执行服务端校验与版本发布。
              </p>
            </div>
          </div>
        ) : (
          <div className={styles.savingState} role="status">
            <div className={styles.savingCopy}>
              <CheckCircle2 aria-hidden="true" color="#389e0d" size={52} />
              <h3>机器人模型已保存</h3>
              <p>
                URDF、描述配置和关节映射已通过校验，模型版本已经发布。
                {savedVersionId ? `版本：${savedVersionId}` : ""}
              </p>
              <Button
                type="primary"
                onClick={() => {
                  setImportOpen(false);
                  resetImport();
                }}
              >
                完成
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </main>
  );
}

export default Component;
