import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import { registerScenario } from "./registry";

export const DATASET_SCENARIOS = [
  "happy",
  "empty",
  "filtered-empty",
  "cursor-pagination",
  "first-loading",
  "partial-error",
  "fatal-error",
  "forbidden",
  "not-found",
  "gone",
  "conflict",
  "rate-limited",
  "offline-recovery",
  "unknown-enum",
  "contract-mismatch",
  "scope-switch-race",
  "validation-error",
  "preflight-blocked",
  "submitting",
  "accepted-job",
  "job-failed",
  "duplicate-idempotency",
  "etag-conflict",
  "permission-revoked",
  "token-expired",
  "double-click",
  "return-partial-response",
  "atomic-failure",
  "feature-unavailable",
] as const;
export type DatasetScenario = (typeof DATASET_SCENARIOS)[number];
let active: DatasetScenario = "happy";
export function setDatasetScenario(value: DatasetScenario) {
  active = value;
}
export function getDatasetScenario(
  search = globalThis.location?.search,
): DatasetScenario {
  if (search) {
    const raw = new URLSearchParams(search).get("mockScenario");
    const requested = raw?.includes(":")
      ? raw.startsWith("datasets:")
        ? raw.slice("datasets:".length)
        : null
      : raw;
    if (
      requested &&
      (DATASET_SCENARIOS as readonly string[]).includes(requested)
    )
      return requested as DatasetScenario;
  }
  return active;
}
export function resetDatasetScenario() {
  active = "happy";
}

const scope = {
  organizationId: "org_fx_01",
  projectId: "prj_fx_01",
  regionCode: "cn-shanghai",
} as const;
const capabilities = [
  "dataset.create",
  "dataset.read",
  "dataset_version.read",
  "dataset_version.review",
  "dataset_version.publish",
  "dataset_version.download_manifest",
  "dataset_version.download_raw",
  "episode.read",
  "data_schema.read",
  "storage.overview.read",
  "export.create",
  "export.read",
  "export.download",
  "manual_issue.create",
  "manual_issue.read",
] as const;
for (const scenario of DATASET_SCENARIOS)
  registerScenario("datasets", scenario, () => {
    setDatasetScenario(scenario);
    const shell = useShellStore.getState();
    shell.setSession(
      {
        actorId: "user_fx_admin",
        displayName: "Fixture Admin",
        roleIds: ["PROJECT_ADMIN"],
      },
      "fixture-bearer",
    );
    shell.setScope(scope);
    shell.setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "role_fx_dataset_01",
      capabilities:
        scenario === "forbidden" || scenario === "permission-revoked"
          ? []
          : capabilities,
      fetchedAt: "2026-08-06T12:00:00Z",
    });
    return resetDatasetScenario;
  });
