import { Form } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type {
  RobotModel,
  RobotModelVersion,
} from "../../../entities/robot-model";
import type { RobotModelAsset, RobotModelJointMapping } from "../api/client";
import { createLazyThreeRobotSceneLoader } from "../../viewer";
import { isDomainError } from "../../../shared/api/domain-error";
import { invalidateScopedDomain } from "../../../shared/api/use-scoped-mutation";
import { useScopeQueryRevision } from "../../../shared/api/use-scoped-query";
import { useShellStore } from "../../../shared/scope/shell-store";
import { hasSessionScopeAccess } from "../../../shared/scope/session-access";
import {
  classifyRobotModelFiles,
  modelAssetSelectionError,
  robotModelFilesFromDrop,
  type PendingRobotModelAsset,
  type RobotModelFileCandidate,
} from "./model-file-selection";
import {
  buildRobotConfigurationAsset,
  createLocalRobotPreview,
  mappingsCoverUrdf,
  parseRobotModelAssets,
  type LocalRobotPreview,
  type ParsedRobotModel,
} from "./urdf-import";
import type { ImportStage, ModelMetadataFormValues } from "./contracts";
import { parseErrorLabels, uniqueVersionLabel } from "./presentation";
import { createRobotModelImportOperation } from "./api-gateway";
import { RobotModelImportFailure, saveRobotModelImport } from "./save-model";

export function useRobotModelImport({
  organizationId,
  enabled,
  onPublished,
  onFailureNotice,
}: {
  readonly organizationId: string | null;
  readonly enabled: boolean;
  readonly onPublished: (version: RobotModelVersion) => void | Promise<void>;
  readonly onFailureNotice: (notice: string) => void;
}) {
  const [modelForm] = Form.useForm<ModelMetadataFormValues>();
  const client = useQueryClient();
  const revision = useScopeQueryRevision();
  const authorized = useShellStore((state) =>
    hasSessionScopeAccess(state, organizationId ? { organizationId } : null),
  );
  const setPageNotice = onFailureNotice;
  const activeOperation = useRef<AbortController | null>(null);
  const ownerGeneration = useRef(0);
  const saveInFlight = useRef(false);
  const mounted = useRef(false);
  const ownerRef = useRef({ organizationId, revision, enabled, authorized });
  ownerRef.current = { organizationId, revision, enabled, authorized };
  const currentOwner = (generation: number, signal?: AbortSignal) =>
    mounted.current &&
    generation === ownerGeneration.current &&
    ownerRef.current.organizationId === organizationId &&
    ownerRef.current.revision === revision &&
    ownerRef.current.enabled &&
    ownerRef.current.authorized &&
    !signal?.aborted;
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
  const modelFileInputRef = useRef<HTMLInputElement>(null);
  const modelFolderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    mounted.current = true;
    const generation = ++previewMountGenerationRef.current;
    return () => {
      mounted.current = false;
      ownerGeneration.current += 1;
      activeOperation.current?.abort();
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
  const modelSavePending = importStage === "saving";

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

  const replaceLocalPreview = (preview: LocalRobotPreview | null) => {
    if (localPreviewRef.current !== preview) localPreviewRef.current?.dispose();
    localPreviewRef.current = preview;
    setLocalPreview(preview);
  };

  const clearAnalysis = () => {
    ownerGeneration.current += 1;
    activeOperation.current?.abort();
    activeOperation.current = null;
    saveInFlight.current = false;
    setParsingModel(false);
    setReadingDroppedFiles(false);
    setLoadingExistingModel(false);
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
    if (!organizationId || !enabled || !authorized) return;
    resetImport();
    const metadata = defaultModelMetadata(model, sourceVersion);
    setImportModel(model);
    setImportSourceVersion(sourceVersion);
    setModelMetadata(metadata);
    setModelCommandId(crypto.randomUUID());
    modelForm.setFieldsValue(metadata);
    setImportOpen(true);
  };

  const cancelImport = async () => {
    if (modelSavePending) return;
    setImportOpen(false);
    resetImport();
  };

  const analyseSelectedFiles = async (
    assets: readonly PendingRobotModelAsset[] = modelAssets,
    preferredMappings: readonly RobotModelJointMapping[] = [],
    signal?: AbortSignal,
  ) => {
    const generation = ownerGeneration.current;
    setParsingModel(true);
    setModelError(null);
    try {
      const parsed = await parseRobotModelAssets(assets);
      if (!currentOwner(generation, signal)) return;
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
      if (!currentOwner(generation, signal)) return;
      const code = error instanceof Error ? error.message : "";
      setModelError(
        parseErrorLabels[code] ??
          (error instanceof SyntaxError
            ? "JSON 配置文件格式错误，请修正后重新选择。"
            : "模型文件解析失败，请检查 URDF 与配置文件。"),
      );
    } finally {
      if (currentOwner(generation)) setParsingModel(false);
    }
  };

  const reviewSelectedFiles = async () => {
    const generation = ownerGeneration.current;
    try {
      const metadata = await modelForm.validateFields();
      if (!currentOwner(generation)) return;
      setModelMetadata(metadata);
      await analyseSelectedFiles();
    } catch {
      if (currentOwner(generation))
        setModelError("请先补全模型名称和版本信息。");
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
    const generation = ownerGeneration.current;
    setModelDropActive(false);
    setReadingDroppedFiles(true);
    setModelError(null);
    try {
      const candidates = await robotModelFilesFromDrop(dataTransfer);
      if (!currentOwner(generation)) return;
      addModelFileCandidates(candidates);
      if (!candidates.length) {
        setModelNotice("没有读取到文件，请改用“选择文件夹”。");
      }
    } catch {
      if (currentOwner(generation))
        setModelError("无法读取拖入的文件夹，请改用“选择文件夹”重试。");
    } finally {
      if (currentOwner(generation)) setReadingDroppedFiles(false);
    }
  };

  const startOperation = (generation: number, organization: string) => {
    const controller = new AbortController();
    activeOperation.current?.abort();
    activeOperation.current = controller;
    const operation = createRobotModelImportOperation(
      organization,
      controller.signal,
    );
    const onAbort = () => {
      if (!currentOwner(generation)) return;
      setImportOpen(false);
      resetImport();
    };
    operation.signal.addEventListener("abort", onAbort, { once: true });
    if (operation.signal.aborted) onAbort();
    return {
      ...operation,
      release: () => {
        operation.signal.removeEventListener("abort", onAbort);
        operation.release();
        // Cancel siblings of a failed download and release transport ownership.
        controller.abort();
        if (activeOperation.current === controller) {
          activeOperation.current = null;
          saveInFlight.current = false;
        }
      },
    };
  };

  const editModel = async (
    model: RobotModel,
    version: RobotModelVersion,
    assets: readonly RobotModelAsset[],
    preferredMappings: readonly RobotModelJointMapping[],
  ) => {
    if (!organizationId || !enabled || !authorized) return;
    beginImport(model, version);
    const generation = ownerGeneration.current;
    const operation = startOperation(generation, organizationId);
    if (!currentOwner(generation, operation.signal)) {
      operation.release();
      return;
    }
    setLoadingExistingModel(true);
    try {
      const downloaded = await operation.downloadAssets(version.id, assets);
      if (!currentOwner(generation, operation.signal)) return;
      const classified = classifyRobotModelFiles(downloaded);
      setModelAssets(classified.assets);
      await analyseSelectedFiles(
        classified.assets,
        preferredMappings,
        operation.signal,
      );
    } catch (error) {
      if (currentOwner(generation, operation.signal))
        setModelError(
          isDomainError(error)
            ? error.message
            : "当前模型文件读取失败，请稍后重试。",
        );
    } finally {
      if (currentOwner(generation)) setLoadingExistingModel(false);
      operation.release();
    }
  };

  const saveRobotModel = async () => {
    if (
      !analysis ||
      !organizationId ||
      !enabled ||
      !authorized ||
      !modelCommandId ||
      !modelMetadata ||
      !mappingsValid ||
      saveInFlight.current
    )
      return;
    const generation = ownerGeneration.current;
    const operation = startOperation(generation, organizationId);
    if (!currentOwner(generation, operation.signal)) {
      operation.release();
      return;
    }
    saveInFlight.current = true;
    setImportStage("saving");
    setModelError(null);
    try {
      let published: RobotModelVersion;
      try {
        published = await saveRobotModelImport(
          {
            analysis,
            assets: modelAssets,
            mappings,
            metadata: modelMetadata,
            model: importModel,
            sourceVersion: importSourceVersion,
            commandId: modelCommandId,
          },
          operation.gateway,
          operation.signal,
        );
      } catch (error) {
        if (!currentOwner(generation, operation.signal)) return;
        const failure =
          error instanceof RobotModelImportFailure ? error.failure : error;
        const failureMessage = isDomainError(failure)
          ? failure.message
          : failure instanceof Error && failure.message
            ? `保存失败：${failure.message}`
            : "模型保存失败，请检查文件后重试。";
        const cleaned =
          error instanceof RobotModelImportFailure && error.cleaned;
        setImportOpen(false);
        resetImport();
        setPageNotice(
          cleaned
            ? `${failureMessage} 未完成的模型版本和服务器临时文件已自动清理，请重新添加。`
            : `${failureMessage} 自动清理未能完全确认；系统已阻止失败项显示，请联系管理员检查服务器存储。`,
        );
        return;
      }
      if (!currentOwner(generation, operation.signal)) return;
      setSavedVersionId(published.id);
      setImportStage("saved");
      try {
        // A refresh failure cannot roll back a successfully published immutable version.
        await invalidateScopedDomain(
          client,
          {
            scope: operation.scope,
            sessionRevision: operation.sessionRevision,
            revision,
          },
          "robot-models",
        );
        if (currentOwner(generation, operation.signal))
          await onPublished(published);
      } catch {
        if (currentOwner(generation, operation.signal))
          setPageNotice("机器人模型已保存，列表暂未刷新，请刷新页面查看。");
      }
    } finally {
      operation.release();
    }
  };

  useEffect(() => {
    activeOperation.current?.abort();
    setImportOpen(false);
    resetImport();
    // Invalidate local parsing as well as network operations when ownership changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [organizationId, revision, enabled, authorized]);
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

  return {
    importOpen,
    importStage,
    importSourceVersion,
    modelForm,
    modelMetadata,
    modelAssets,
    modelAssetCounts,
    analysis,
    mappings,
    mappingsValid,
    localModelRef,
    localRuntimeLoader,
    configurationAsset,
    configurationPreview,
    modelCommandId,
    modelDropActive,
    readingDroppedFiles,
    loadingExistingModel,
    parsingModel,
    modelNotice,
    modelError,
    savedVersionId,
    modelSavePending,
    modelSelectionError,
    modelFileInputRef,
    modelFolderInputRef,
    setImportOpen,
    setModelDropActive,
    setModelAssets,
    setMappings,
    setModelError,
    clearAnalysis,
    resetImport,
    beginImport,
    editModel,
    cancelImport,
    addModelFilesFromPicker,
    handleModelFilesDrop,
    reviewSelectedFiles,
    saveRobotModel,
  };
}

export type RobotModelImportController = ReturnType<typeof useRobotModelImport>;
