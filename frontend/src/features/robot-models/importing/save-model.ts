import { sha256File } from "../../../shared/lib/browser-crypto";
import type { RobotModelAssetUploadInput } from "../api/client";
import type {
  RobotModelImportGateway,
  RobotModelImportInput,
} from "./contracts";
import {
  buildRobotConfigurationAsset,
  mappingsCoverUrdf,
  replaceRobotConfigurationAsset,
} from "./urdf-import";

export class RobotModelImportFailure extends Error {
  constructor(
    readonly failure: unknown,
    readonly cleaned: boolean,
  ) {
    super(failure instanceof Error ? failure.message : "模型保存失败");
    this.name = "RobotModelImportFailure";
  }
}

/** Save/publish policy independent of pages, router, React and transport selection. */
export async function saveRobotModelImport(
  input: RobotModelImportInput,
  gateway: RobotModelImportGateway,
  signal: AbortSignal,
) {
  let draftVersionId: string | null = null;
  const values = { ...input.metadata };
  const mappings = input.mappings.map((mapping) => ({ ...mapping }));
  const model = input.model ? { ...input.model } : null;
  const sourceVersion = input.sourceVersion;
  const { analysis, commandId } = input;
  const check = () => signal.throwIfAborted();
  try {
    check();
    if (
      !commandId ||
      !mappingsCoverUrdf(mappings, analysis.actuatedJointNames) ||
      analysis.missingMeshReferences.length
    ) {
      throw new Error("模型文件或关节映射尚未完成检查");
    }
    const configurationAsset = buildRobotConfigurationAsset(
      analysis,
      mappings,
      {
        ...(model ? { modelId: model.id } : {}),
        displayName:
          values.displayName ?? model?.displayName ?? analysis.robotName,
        manufacturer: values.manufacturer ?? model?.manufacturer ?? "",
        modelCode: values.modelCode ?? model?.modelCode ?? analysis.robotName,
      },
    );
    const assetsToSave = replaceRobotConfigurationAsset(
      [...input.assets],
      configurationAsset,
    );
    const draft = sourceVersion
      ? await gateway.createDraft({
          sourceVersionId: sourceVersion.id,
          versionLabel: values.versionLabel.trim(),
          updateScope: "ASSETS",
          idempotencyKey: commandId,
        })
      : await gateway.createModel({
          manufacturer: values.manufacturer?.trim() ?? "",
          modelCode: values.modelCode?.trim() ?? "",
          displayName: values.displayName?.trim() ?? "",
          versionLabel: values.versionLabel.trim(),
          idempotencyKey: commandId,
        });
    draftVersionId = draft.id;
    check();
    const files: RobotModelAssetUploadInput[] = [];
    for (const asset of assetsToSave) {
      check();
      files.push({ ...asset, sha256: await sha256File(asset.file) });
    }
    check();
    await gateway.uploadAssets(draft.id, files, `${commandId}:assets`);
    check();
    const uploadedDraft = await gateway.getVersion(draft.id);
    check();
    const configuredDraft = await gateway.replaceMappings({
      versionId: draft.id,
      etag: uploadedDraft.etag,
      mappings,
      idempotencyKey: `${commandId}:mappings`,
    });
    check();
    // Backend preflight token binds to the SAME identity consumed by publication.
    const preflight = await gateway.preflight({
      versionId: draft.id,
      etag: configuredDraft.etag,
      idempotencyKey: `${commandId}:publish`,
    });
    check();
    if (!preflight.allowed || !preflight.preflight_token) {
      const failed = preflight.checks.filter((item) => !item.passed);
      throw new Error(
        failed.length
          ? failed.map((item) => item.message).join("；")
          : "服务端校验未通过。",
      );
    }
    // No page refresh or callback belongs inside this publication/rollback boundary.
    return await gateway.publish({
      versionId: draft.id,
      etag: preflight.expected_etag,
      preflightToken: preflight.preflight_token,
      idempotencyKey: `${commandId}:publish`,
    });
  } catch (failure) {
    let cleaned = draftVersionId === null;
    if (draftVersionId && !signal.aborted) {
      for (let attempt = 0; attempt < 2 && !cleaned; attempt += 1) {
        try {
          check();
          await gateway.discard(draftVersionId, sourceVersion?.id);
          cleaned = true;
        } catch {
          /* Keep bounded cleanup retry; never borrow a replacement session. */
        }
      }
    }
    throw new RobotModelImportFailure(failure, cleaned);
  }
}
