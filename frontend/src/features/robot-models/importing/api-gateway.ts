import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import * as api from "../api/client";
import type { RobotModelImportGateway } from "./contracts";
import type { RobotModelFileCandidate } from "./model-file-selection";

/** Each import owns one session lease across commands and signed transfers. */
export function createRobotModelImportOperation(
  organizationId: string,
  signal?: AbortSignal,
) {
  const lease = createSessionScopeLease({ organizationId }, signal);
  const run = async <T>(operation: () => Promise<T>): Promise<T> => {
    lease.signal.throwIfAborted();
    const result = await operation();
    lease.signal.throwIfAborted();
    return result;
  };
  const gateway: RobotModelImportGateway = {
    createModel: (intent) =>
      run(() => api.createRobotModel(organizationId, intent, lease.signal)),
    createDraft: (intent) =>
      run(() =>
        api.createRobotModelDraft(organizationId, intent, lease.signal),
      ),
    uploadAssets: (version, files, key) =>
      run(() =>
        api.uploadRobotModelAssets(
          organizationId,
          version,
          files,
          key,
          lease.signal,
        ),
      ),
    getVersion: (version) =>
      run(() =>
        api.getRobotModelVersion(organizationId, version, lease.signal),
      ),
    replaceMappings: (intent) =>
      run(() =>
        api.replaceRobotModelJointMappings(
          organizationId,
          intent,
          lease.signal,
        ),
      ),
    preflight: (intent) =>
      run(() =>
        api.preflightRobotModelPublish(organizationId, intent, lease.signal),
      ),
    publish: (intent) =>
      run(() =>
        api.publishRobotModelVersion(organizationId, intent, lease.signal),
      ),
    discard: (version, fallback) =>
      run(() =>
        api.discardRobotModelImport(
          organizationId,
          version,
          fallback,
          lease.signal,
        ),
      ),
  };
  return {
    ...lease,
    gateway,
    downloadAssets: (
      versionId: string,
      assets: readonly api.RobotModelAsset[],
    ) =>
      run(async () =>
        Promise.all(
          assets.map(async (asset): Promise<RobotModelFileCandidate> => {
            lease.signal.throwIfAborted();
            const authorization = await api.authorizeRobotModelAssetDownload(
              organizationId,
              versionId,
              asset.asset_id,
              lease.signal,
            );
            lease.signal.throwIfAborted();
            const response = await fetch(authorization.download_url, {
              cache: "no-store",
              credentials: "omit",
              signal: lease.signal,
            });
            if (!response.ok) throw new Error("ASSET_DOWNLOAD_FAILED");
            const blob = await response.blob();
            lease.signal.throwIfAborted();
            return {
              file: new File([blob], asset.relative_path.split("/").at(-1)!, {
                type: asset.media_type,
              }),
              relativePath: asset.relative_path,
            };
          }),
        ),
      ),
  };
}
