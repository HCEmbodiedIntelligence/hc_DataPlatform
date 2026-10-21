import { emptyDocument, newFeature } from "./editor-model";
import type { SchemeDetail, SchemeLink, SchemeVersion } from "./contracts";
import { versionSchema } from "./contracts";
export function fixtureDocument() {
  const doc = emptyDocument();
  doc.name = "X1 关节方案";
  doc.software = "X1 SDK";
  doc.output.frequency_hz = 15;
  doc.output.features = {
    "observation.state": {
      ...newFeature("state"),
      shape: [2],
      names: ["left", "right"],
      units: ["rad", "rad"],
    },
    action: {
      ...newFeature("action"),
      shape: [1],
      names: ["grip"],
      units: ["m"],
    },
  };
  return doc;
}
export function fixtureVersion(version = 1): SchemeVersion {
  return versionSchema.parse({
    ...fixtureDocument(),
    scheme_id: "shared",
    version,
    content_sha256: String(version).repeat(64),
    draft_revision: version,
    published_at: "2026-10-21T00:00:00Z",
    output_contract_sha256: "a".repeat(64),
    validations: {},
  });
}
export function fixtureDetail(): SchemeDetail {
  return {
    scheme_id: "shared",
    revision: 1,
    draft: fixtureDocument(),
    latest_version: 1,
    archived: false,
    validations: [],
    versions: [fixtureVersion()],
  };
}
export function fixtureLink(mapping = 1, version = 1): SchemeLink {
  const scheme = fixtureVersion(version);
  return {
    scheme_id: "shared",
    scheme_version: version,
    revision: 1,
    mapping_version: mapping,
    is_default: true,
    active: true,
    archived: false,
    status: "READY",
    validated_formats: ["LEROBOT_V3"],
    validations: [],
    scheme,
    mapping: {
      scheme: {
        scheme_id: "shared",
        version,
        content_sha256: scheme.content_sha256,
      },
      mapping: { sources: {} },
      mapping_version: mapping,
      content_sha256: "f".repeat(64),
    },
  };
}
