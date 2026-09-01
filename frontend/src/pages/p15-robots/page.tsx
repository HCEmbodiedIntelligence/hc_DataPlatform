import type { ColumnDef } from "@tanstack/react-table";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Steps,
} from "antd";
import {
  Box,
  CheckCircle2,
  CircleDot,
  File as FileIcon,
  FolderOpen,
  Radio,
  Search,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RobotModelVersion } from "../../entities/robot-model";
import type { Robot } from "../../entities/robot";
import {
  authorizeRobotModelAssetDownload,
  authorizeRobotModelViewerAssets,
  getRobotModelVersion,
  useBindRobotModelVersion,
  useCreateRobotModel,
  useCreateRobotModelDraft,
  usePreflightRobotModelPublish,
  usePublishRobotModelVersion,
  useReplaceRobotModelJointMappings,
  useRobotModelAssets,
  useRobotModelJointMappings,
  useRobotModelVersion,
  useUploadRobotModelAssets,
  type RobotModelJointMapping,
} from "../../features/robot-models/api";
import {
  useCreateRobot,
  useRobotBootstrap,
  useRobots,
} from "../../features/robots/api";
import {
  createLazyThreeRobotSceneLoader,
  RobotSceneCore,
} from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  CursorPager,
  DataTable,
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
import { robotsQueryCodec, type RobotsSearch } from "./query-codec";
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

type LifecycleFilter = "all" | NonNullable<RobotsSearch["lifecycleStatus"]>;
type ConnectivityFilter =
  | "all"
  | NonNullable<RobotsSearch["connectivityState"]>;
type RobotRow = NonNullable<
  ReturnType<typeof useRobots>["data"]
>["items"][number];
type ImportStage = "files" | "review" | "saving" | "saved";

interface CreateRobotFormValues {
  readonly displayName: string;
  readonly serialNo: string;
}

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
  const canManageRobots = capabilities.has("robot.manage");
  const canManageModels = capabilities.has("robot_model.manage");

  const [query, setQuery] = useState(search.q ?? "");
  const [lifecycleStatus, setLifecycleStatus] = useState<LifecycleFilter>(
    search.lifecycleStatus ?? "all",
  );
  const [connectivityState, setConnectivityState] =
    useState<ConnectivityFilter>(search.connectivityState ?? "all");
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreateRobotFormValues>();
  const [modelForm] = Form.useForm<ModelMetadataFormValues>();

  const robots = useRobots({
    ...(search.q ? { q: search.q } : {}),
    ...(search.lifecycleStatus
      ? { lifecycle_status: search.lifecycleStatus }
      : {}),
    ...(search.connectivityState
      ? { connectivity_state: search.connectivityState }
      : {}),
  });
  const robotItems = robots.data?.items ?? [];
  const selectedRobot =
    robotItems.find((robot) => robot.id === search.robotId) ?? robotItems[0];
  const selectedRobotId = selectedRobot?.id ?? null;
  const bootstrap = useRobotBootstrap(selectedRobotId);
  const selectedBootstrap = bootstrap.data;
  const boundVersionId =
    selectedBootstrap?.effectiveModelBinding?.robotModelVersionId ?? null;
  const boundVersion = useRobotModelVersion(boundVersionId);
  const currentAssets = useRobotModelAssets(boundVersionId);
  const currentMappings = useRobotModelJointMappings(boundVersionId);
  const currentUrdf = currentAssets.data?.find(
    (asset) => asset.role === "URDF",
  );

  const createRobot = useCreateRobot();
  const createRobotModel = useCreateRobotModel();
  const createModelDraft = useCreateRobotModelDraft();
  const uploadModelAssets = useUploadRobotModelAssets();
  const replaceModelMappings = useReplaceRobotModelJointMappings();
  const publishPreflight = usePreflightRobotModelPublish();
  const publishVersion = usePublishRobotModelVersion();
  const bindVersion = useBindRobotModelVersion();

  const [importOpen, setImportOpen] = useState(false);
  const [importStage, setImportStage] = useState<ImportStage>("files");
  const [importRobot, setImportRobot] = useState<Robot | null>(null);
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
  const [configurationPreview, setConfigurationPreview] = useState("");
  const [modelCommandId, setModelCommandId] = useState("");
  const [modelDropActive, setModelDropActive] = useState(false);
  const [readingDroppedFiles, setReadingDroppedFiles] = useState(false);
  const [loadingExistingModel, setLoadingExistingModel] = useState(false);
  const [parsingModel, setParsingModel] = useState(false);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [savedVersionId, setSavedVersionId] = useState<string | null>(null);
  const modelFileInputRef = useRef<HTMLInputElement>(null);
  const modelFolderInputRef = useRef<HTMLInputElement>(null);

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
    publishVersion.isPending ||
    bindVersion.isPending;

  useEffect(() => {
    if (!selectedRobot || search.robotId === selectedRobot.id) return;
    setParams(
      robotsQueryCodec.build(
        {
          ...search,
          robotId: selectedRobot.id,
          componentId: undefined,
          tab: "overview",
        },
        search,
      ),
      { replace: true },
    );
  }, [search, selectedRobot, setParams]);

  useEffect(
    () => () => {
      localPreview?.dispose();
    },
    [localPreview],
  );

  const currentModelRef = useMemo(
    () =>
      boundVersion.data
        ? {
            modelId: boundVersion.data.robotModelId,
            modelVersion: boundVersion.data.id,
          }
        : null,
    [boundVersion.data],
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
    if (!boundVersion.data || !currentUrdf || !organizationId) return;
    const versionId = boundVersion.data.id;
    const modelId = boundVersion.data.robotModelId;
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
    boundVersion.data,
    currentAssets.data,
    currentMappings.data,
    currentUrdf,
    organizationId,
  ]);

  const localModelRef = useMemo(
    () => ({
      modelId: importRobot?.id ?? "local-robot",
      modelVersion: modelCommandId || "local-preview",
    }),
    [importRobot?.id, modelCommandId],
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
    if (!analysis || !importRobot) {
      setConfigurationPreview("");
      return;
    }
    const asset = buildRobotConfigurationAsset(analysis, mappings, {
      robotId: importRobot.id,
      displayName: importRobot.displayName,
      serialNo: importRobot.serialNo,
    });
    let active = true;
    void asset.file.text().then((content) => {
      if (active) setConfigurationPreview(content);
    });
    return () => {
      active = false;
    };
  }, [analysis, importRobot, mappings]);

  const selectRobot = (robotId: string) => {
    setParams(
      robotsQueryCodec.build(
        {
          ...search,
          robotId,
          componentId: undefined,
          tab: "overview",
        },
        search,
      ),
    );
  };

  const clearAnalysis = () => {
    setAnalysis(null);
    setMappings([]);
    setLocalPreview(null);
    setConfigurationPreview("");
    setImportStage("files");
    setSavedVersionId(null);
  };

  const resetImport = () => {
    clearAnalysis();
    setImportRobot(null);
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
    robot: Robot,
    sourceVersion: RobotModelVersion | null,
  ): ModelMetadataFormValues => ({
    ...(sourceVersion
      ? {}
      : {
          manufacturer: "HC Robotics",
          modelCode: robot.serialNo,
          displayName: `${robot.displayName} 模型`,
        }),
    versionLabel: uniqueVersionLabel(sourceVersion ? "update" : "1.0.0"),
  });

  const beginImport = (
    robot: Robot,
    sourceVersion: RobotModelVersion | null,
  ) => {
    resetImport();
    const metadata = defaultModelMetadata(robot, sourceVersion);
    setImportRobot(robot);
    setImportSourceVersion(sourceVersion);
    setModelMetadata(metadata);
    setModelCommandId(crypto.randomUUID());
    modelForm.setFieldsValue(metadata);
    setImportOpen(true);
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
      setLocalPreview(preview);
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
      !selectedBootstrap ||
      !boundVersion.data ||
      !currentAssets.data ||
      !organizationId
    ) {
      return;
    }
    beginImport(selectedBootstrap, boundVersion.data);
    setLoadingExistingModel(true);
    try {
      const downloaded = await Promise.all(
        currentAssets.data.map(async (asset) => {
          const authorization = await authorizeRobotModelAssetDownload(
            organizationId,
            boundVersion.data!.id,
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
      !importRobot ||
      !organizationId ||
      !modelCommandId ||
      !modelMetadata ||
      !mappingsValid
    ) {
      return;
    }
    const values = modelMetadata;
    setImportStage("saving");
    setModelError(null);
    try {
      const configurationAsset = buildRobotConfigurationAsset(
        analysis,
        mappings,
        {
          robotId: importRobot.id,
          displayName: importRobot.displayName,
          serialNo: importRobot.serialNo,
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
      await bindVersion.mutateAsync({
        versionId: published.id,
        robotId: importRobot.id,
        robotEtag: importRobot.etag,
        idempotencyKey: `${modelCommandId}:bind`,
      });
      setSavedVersionId(published.id);
      setImportStage("saved");
      await bootstrap.refetch();
    } catch (error) {
      setImportStage("review");
      setModelError(
        isDomainError(error)
          ? error.message
          : error instanceof Error && error.message
            ? `保存失败：${error.message}`
            : "模型保存失败，请检查文件后重试。",
      );
    }
  };

  const submitCreateRobot = async (values: CreateRobotFormValues) => {
    try {
      const robot = await createRobot.mutateAsync({
        displayName: values.displayName.trim(),
        serialNo: values.serialNo.trim(),
        lifecycleStatus: "DRAFT",
        connectivityState: "OFFLINE",
        idempotencyKey: crypto.randomUUID(),
      });
      setCreateOpen(false);
      createForm.resetFields();
      selectRobot(robot.id);
      beginImport(robot, null);
    } catch {
      return;
    }
  };

  const columns = useMemo<ColumnDef<RobotRow, unknown>[]>(
    () => [
      {
        id: "displayName",
        header: "机器人",
        size: 180,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            aria-current={
              row.original.id === selectedRobotId ? "true" : undefined
            }
            onClick={() => selectRobot(row.original.id)}
          >
            {row.original.displayName}
          </Button>
        ),
      },
      {
        id: "serialNo",
        header: "序列号",
        size: 145,
        cell: ({ row }) => row.original.serialNo,
      },
      {
        id: "connectivity",
        header: "状态",
        size: 90,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.connectivity}
            label={row.original.connectivity === "ONLINE" ? "在线" : "离线"}
            tone={
              row.original.connectivity === "ONLINE" ? "success" : "neutral"
            }
          />
        ),
      },
    ],
    [selectedRobotId],
  );

  const pageState = !bootstrapLoaded ? (
    <PageState state="loading" label="机器人资产" />
  ) : !organizationId ? (
    <PageState
      state="empty"
      title="尚未加入组织"
      description="机器人是组织级通用资产；加入组织后即可查看，无需先选择项目。"
    />
  ) : robots.isPending ? (
    <PageState state="loading" label="机器人资产" />
  ) : robots.error && isDomainError(robots.error) ? (
    <PageState
      state={robots.error.httpStatus === 403 ? "forbidden" : "error"}
      onRetry={() => void robots.refetch()}
    />
  ) : null;

  const configurationAsset =
    analysis && importRobot
      ? buildRobotConfigurationAsset(analysis, mappings, {
          robotId: importRobot.id,
          displayName: importRobot.displayName,
          serialNo: importRobot.serialNo,
        })
      : null;

  return (
    <main className={workspace.page} data-page-id="P15">
      <StandardPageScaffold
        header={{
          title: "机器人资产",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "robots", label: "机器人资产" },
          ],
          actions: (
            <>
              <Button
                type="primary"
                disabled={!canManageRobots}
                onClick={() => {
                  createForm.resetFields();
                  setCreateOpen(true);
                }}
              >
                添加机器人
              </Button>
              <Button
                icon={<Upload aria-hidden="true" size={16} />}
                disabled={!selectedBootstrap || !canManageModels}
                onClick={() => {
                  if (selectedBootstrap) beginImport(selectedBootstrap, null);
                }}
              >
                导入 URDF / 配置
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Box aria-hidden="true" size={20} />}
              label="机器人"
              value={String(robotItems.length)}
            />
            <SummaryItem
              icon={<Radio aria-hidden="true" size={20} />}
              label="在线"
              value={String(
                robotItems.filter((robot) => robot.connectivity === "ONLINE")
                  .length,
              )}
            />
            <SummaryItem
              icon={<FileIcon aria-hidden="true" size={20} />}
              label="当前模型"
              value={boundVersionId ? "已配置" : "未配置"}
            />
            <SummaryItem
              icon={<CircleDot aria-hidden="true" size={20} />}
              label="操作流程"
              value="解析 → 预览 → 保存"
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
                    lifecycleStatus:
                      lifecycleStatus === "all" ? undefined : lifecycleStatus,
                    connectivityState:
                      connectivityState === "all"
                        ? undefined
                        : connectivityState,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              )
            }
            onReset={() => {
              setQuery("");
              setLifecycleStatus("all");
              setConnectivityState("all");
              setParams(
                robotsQueryCodec.build(
                  {
                    ...search,
                    q: undefined,
                    lifecycleStatus: undefined,
                    connectivityState: undefined,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              );
            }}
          >
            <label className={workspace.toolbarField}>
              <span>机器人名称</span>
              <Input.Search
                className={workspace.toolbarSearch}
                value={query}
                placeholder="搜索机器人"
                enterButton={
                  <Button
                    aria-label="搜索机器人"
                    icon={<Search aria-hidden="true" size={15} />}
                  />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>生命周期</span>
              <Select
                value={lifecycleStatus}
                options={[
                  { value: "all", label: "全部" },
                  { value: "DRAFT", label: "草稿" },
                  { value: "ACTIVE", label: "启用" },
                  { value: "MAINTENANCE", label: "维护中" },
                  { value: "DISABLED", label: "已停用" },
                  { value: "RETIRED", label: "已退役" },
                ]}
                onChange={setLifecycleStatus}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>连接状态</span>
              <Select
                value={connectivityState}
                options={[
                  { value: "all", label: "全部" },
                  { value: "ONLINE", label: "在线" },
                  { value: "OFFLINE", label: "离线" },
                  { value: "DEGRADED", label: "降级" },
                ]}
                onChange={setConnectivityState}
              />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={styles.assetWorkspace}>
          <section className={styles.assetList} aria-label="机器人列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>机器人</h2>
              </div>
              <span className={workspace.inlineMeta}>
                共 {robotItems.length} 台
              </span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={robotItems}
                columns={columns}
                getRowId={(robot) => robot.id}
                caption="机器人资产列表"
                state={robotItems.length ? "ready" : "empty"}
                empty={
                  <PageState
                    state={search.q ? "filtered-empty" : "empty"}
                    title={search.q ? "没有匹配的机器人" : "还没有机器人"}
                    description="添加机器人后即可导入 URDF 和描述配置。"
                  />
                }
              />
            </div>
            {robots.data ? (
              <footer className={workspace.tableFooter}>
                <CursorPager
                  pageInfo={{
                    startCursor: robots.data.pageInfo.start_cursor,
                    endCursor: robots.data.pageInfo.end_cursor,
                    hasPreviousPage: robots.data.pageInfo.has_previous_page,
                    hasNextPage: robots.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(
                      robotsQueryCodec.build({ ...search, ...cursor }, search),
                    )
                  }
                  windowLabel={`当前 ${robotItems.length} 台`}
                />
              </footer>
            ) : null}
          </section>

          <section className={styles.assetDetail} aria-label="机器人模型详情">
            <header className={styles.detailHeader}>
              <div>
                <h2>{selectedRobot?.displayName ?? "选择机器人"}</h2>
                <p>
                  {selectedRobot ? selectedRobot.serialNo : "从左侧选择机器人"}
                </p>
              </div>
              {selectedBootstrap ? (
                <StatusTag
                  status={selectedBootstrap.connectivity.state}
                  label={
                    selectedBootstrap.connectivity.state === "ONLINE"
                      ? "在线"
                      : "离线"
                  }
                  tone={
                    selectedBootstrap.connectivity.state === "ONLINE"
                      ? "success"
                      : "neutral"
                  }
                />
              ) : null}
            </header>

            <div className={styles.previewStage}>
              {bootstrap.isPending ||
              (Boolean(boundVersionId) &&
                (boundVersion.isPending || currentAssets.isPending)) ? (
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
                  <Box aria-hidden="true" size={48} />
                  <strong>尚未导入机器人模型</strong>
                  <span>
                    导入 URDF 和描述配置后，系统会先解析结构并显示 3D
                    预览；确认正常后再保存。
                  </span>
                  {selectedBootstrap ? (
                    <Button
                      type="primary"
                      icon={<Upload aria-hidden="true" size={16} />}
                      disabled={!canManageModels}
                      onClick={() => beginImport(selectedBootstrap, null)}
                    >
                      导入 URDF / 配置
                    </Button>
                  ) : null}
                </div>
              )}
            </div>

            <dl className={styles.modelFacts}>
              <div className={styles.modelFact}>
                <dt>机器人 ID</dt>
                <dd title={selectedRobot?.id}>{selectedRobot?.id ?? "—"}</dd>
              </div>
              <div className={styles.modelFact}>
                <dt>URDF</dt>
                <dd>{currentUrdf?.relative_path ?? "未配置"}</dd>
              </div>
              <div className={styles.modelFact}>
                <dt>描述配置</dt>
                <dd>
                  {currentAssets.data?.find((asset) => asset.role === "CONFIG")
                    ?.relative_path ?? "未配置"}
                </dd>
              </div>
              <div className={styles.modelFact}>
                <dt>关节映射</dt>
                <dd>{currentMappings.data?.length ?? 0} 项</dd>
              </div>
            </dl>

            {selectedBootstrap ? (
              <div className={styles.modelActions}>
                {boundVersion.data ? (
                  <Button
                    type="primary"
                    icon={<FileIcon aria-hidden="true" size={16} />}
                    loading={loadingExistingModel}
                    disabled={
                      !canManageModels ||
                      !currentAssets.data?.length ||
                      currentMappings.isPending
                    }
                    onClick={() => void editCurrentModel()}
                  >
                    可视化检查 / 修改模型
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    icon={<Upload aria-hidden="true" size={16} />}
                    disabled={!canManageModels}
                    onClick={() => beginImport(selectedBootstrap, null)}
                  >
                    导入 URDF / 配置
                  </Button>
                )}
                <span className={workspace.inlineMeta}>
                  修改 URDF 或关节映射后会重写描述配置文件，并在保存前重新校验。
                </span>
              </div>
            ) : null}
          </section>
        </div>
      </StandardPageScaffold>

      <Modal
        open={createOpen}
        title="添加机器人"
        footer={null}
        destroyOnHidden
        onCancel={() => {
          if (!createRobot.isPending) setCreateOpen(false);
        }}
      >
        <Alert
          type="info"
          showIcon
          title="机器人是模型文件与描述配置的唯一载体"
          description="创建后会立即进入 URDF 导入流程。"
        />
        <Form<CreateRobotFormValues>
          form={createForm}
          layout="vertical"
          onFinish={(values) => void submitCreateRobot(values)}
          requiredMark="optional"
        >
          <Form.Item
            name="displayName"
            label="机器人名称"
            rules={[{ required: true, whitespace: true, max: 256 }]}
          >
            <Input autoFocus autoComplete="off" maxLength={256} />
          </Form.Item>
          <Form.Item
            name="serialNo"
            label="序列号"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input autoComplete="off" maxLength={128} />
          </Form.Item>
          {createRobot.error ? (
            <Alert
              type="error"
              showIcon
              title={
                isDomainError(createRobot.error)
                  ? createRobot.error.message
                  : "机器人创建失败，请稍后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              disabled={createRobot.isPending}
              onClick={() => setCreateOpen(false)}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createRobot.isPending}
            >
              创建并导入模型
            </Button>
          </Space>
        </Form>
      </Modal>

      <Modal
        open={importOpen}
        width={1040}
        title={importSourceVersion ? "检查并修改机器人模型" : "导入机器人模型"}
        footer={null}
        destroyOnHidden
        mask={{ closable: !modelSavePending }}
        onCancel={() => {
          if (modelSavePending) return;
          setImportOpen(false);
          resetImport();
        }}
      >
        <Steps
          className={styles.wizardSteps}
          current={importStep(importStage)}
          items={[
            { title: "导入文件", content: "URDF + 描述配置" },
            { title: "解析与预览", content: "检查结构和映射" },
            { title: "保存", content: "校验、发布并绑定" },
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
              <Button
                onClick={() => {
                  setImportOpen(false);
                  resetImport();
                }}
              >
                取消
              </Button>
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
        ) : importStage === "review" && analysis && importRobot ? (
          <div className={styles.wizardSection}>
            <div className={styles.wizardMeta}>
              <div>
                <span>入口 URDF</span>
                <strong title={analysis.urdfPath}>{analysis.urdfPath}</strong>
              </div>
              <div>
                <span>机器人名称</span>
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
                正在写入
                URDF、描述配置和关节映射，并执行服务端校验、发布与机器人绑定。
              </p>
            </div>
          </div>
        ) : (
          <div className={styles.savingState} role="status">
            <div className={styles.savingCopy}>
              <CheckCircle2 aria-hidden="true" color="#389e0d" size={52} />
              <h3>机器人模型已保存</h3>
              <p>
                URDF、描述配置和关节映射已通过校验并绑定到机器人。
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
