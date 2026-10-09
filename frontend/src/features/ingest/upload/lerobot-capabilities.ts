import type { IngestScope } from "../../../entities/data-source";
import type { components } from "../../../shared/api/generated/platform";
import { LeRobotCapabilitiesWire } from "../../../shared/api/generated/platform-wires";
import { request } from "../../../shared/api/http-client";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { parseWire } from "../../../shared/api/validate";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";
import { getShellState } from "../../../shared/scope/shell-store";
import { nativeRoot } from "../processing/lerobot-processing";

type Schemas = components["schemas"];
export type LeRobotCapabilities = Schemas["LeRobotCapabilities"];
const capabilitiesWire = LeRobotCapabilitiesWire.refine(
  (value) =>
    Boolean(value.capability_revision) &&
    (value.profile_id == null || Boolean(value.profile_id)) &&
    value.reasons.every((reason) => Boolean(reason.code)),
).refine(
  (value) =>
    !value.processing_supported ||
    (value.storage_supported &&
      Boolean(value.profile_id) &&
      value.output_storage_format === "lerobot_v3"),
);

export async function detectLeRobotCapabilities(
  scope: IngestScope,
  info: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<LeRobotCapabilities> {
  const endpoint = `${nativeRoot(scope)}:capabilities`;
  const capabilityRevision = getShellState().capabilityRevision;
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    const raw = await request<unknown>({
      method: "POST",
      path: endpoint,
      body: { info } satisfies Schemas["LeRobotCapabilityRequest"],
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
    });
    lease.signal.throwIfAborted();
    if (getShellState().capabilityRevision !== capabilityRevision)
      throw new DOMException("Upload authorization changed", "AbortError");
    return parseWire(capabilitiesWire, raw, { endpoint });
  } finally {
    lease.release();
  }
}

export function useLeRobotCapabilities(
  scope: IngestScope,
  info: Readonly<Record<string, unknown>> | null,
  enabled: boolean,
) {
  return useScopedQuery(scope, "ingest", "lerobot-capabilities", info, {
    enabled: enabled && info !== null,
    queryFn: ({ signal }) => detectLeRobotCapabilities(scope, info!, signal),
    retry: false,
    staleTime: 0,
  });
}
