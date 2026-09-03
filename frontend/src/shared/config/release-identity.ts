export interface FrontendReleaseIdentityV1 {
  readonly format_version: "hc-platform-release-identity/v1";
  readonly release_id: string;
  readonly semantic_version: string;
  readonly git_commit: string;
  readonly chart_version: string;
  readonly release_manifest_digest: string;
  readonly migration_manifest_digest: string;
  readonly component: "frontend";
  readonly component_image_digest: string;
}

let releaseIdentity: Readonly<FrontendReleaseIdentityV1> | null = null;

export function configureReleaseIdentity(
  identity: FrontendReleaseIdentityV1,
): void {
  if (releaseIdentity !== null)
    throw new Error("Release identity is already initialized");
  releaseIdentity = Object.freeze({ ...identity });
}

export function getReleaseIdentity(): Readonly<FrontendReleaseIdentityV1> {
  if (releaseIdentity === null)
    throw new Error("Release identity is not initialized");
  return releaseIdentity;
}

export function getReleaseIdentityIfConfigured(): Readonly<FrontendReleaseIdentityV1> | null {
  return releaseIdentity;
}

export function resetReleaseIdentityForTests(): void {
  releaseIdentity = null;
}
