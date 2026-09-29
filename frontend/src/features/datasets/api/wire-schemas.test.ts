import { describe, expect, it } from "vitest";
import {
  episodeRevisionDataWireSchema,
  episodeRevisionHistoryWireSchema,
  versionEpisodeListItemWireSchema,
} from "./wire-schemas";

describe("Episode package revision binding", () => {
  const identity = {
    organization_id: "organization-a",
    project_id: "project-a",
    region_code: "region-a",
    dataset_id: "dataset_package",
    episode_id: "episode_package",
    rollout_id: "explicit:rollout",
  };
  const data = {
    identity,
    data_version_id: "dv_" + "a".repeat(64),
    version: 2,
    content_hash: "b".repeat(64),
    storage_format: "lerobot_v3",
    schema_snapshot_id: "schema-1",
    package_prefix: "private/fixture",
    frame_count: 4,
  };
  const revision = {
    scope: {
      organization_id: identity.organization_id,
      project_id: identity.project_id,
      region_code: identity.region_code,
    },
    dataset_id: identity.dataset_id,
    episode_id: identity.episode_id,
    version_id: "version_cv_" + "c".repeat(64),
    revision_id: "revision_package",
    ordinal: 0,
    content_sha256: data.content_hash,
    started_at_ns: "0",
    duration_ns: "133333333",
    streams: [
      {
        episode_stream_id: "stream_depth",
        channel_path: "observation.images.head_depth",
        kind: "DEPTH",
        t_start_ns: "0",
        t_end_ns: "133333333",
        aligned_media_binding: null,
        data_binding: null,
        package_binding: {
          data,
          rollout_id: identity.rollout_id,
          feature_key: "observation.images.head_depth",
          modality_key: "observation.images.head_depth",
          value_kind: null,
          start_step: 0,
          end_step: 4,
        },
      },
    ],
  };

  it("accepts a pinned RGB-D package reference without a Lance version", () => {
    expect(
      episodeRevisionDataWireSchema.parse(revision).streams[0]?.package_binding
        ?.data,
    ).toEqual(data);
  });

  it("rejects cross-tenant, cross-Episode, out-of-range or mixed-storage bindings", () => {
    for (const identityChange of [
      { organization_id: "foreign" },
      { region_code: "foreign" },
      { episode_id: "episode_foreign" },
      { rollout_id: "other" },
    ]) {
      const wrong = structuredClone(revision);
      Object.assign(
        wrong.streams[0]!.package_binding.data.identity,
        identityChange,
      );
      expect(episodeRevisionDataWireSchema.safeParse(wrong).success).toBe(
        false,
      );
    }
    const oversized = structuredClone(revision);
    oversized.streams[0]!.package_binding.end_step = 5;
    expect(episodeRevisionDataWireSchema.safeParse(oversized).success).toBe(
      false,
    );
    expect(
      episodeRevisionDataWireSchema.safeParse({
        ...revision,
        streams: [
          {
            ...revision.streams[0],
            aligned_media_binding: {
              rollout_id: identity.rollout_id,
              dataset_version: 1,
              artifact_id: "media-1",
              camera_id: "head",
              fps: 30,
              start_step: 0,
              end_step: 4,
            },
          },
        ],
      }).success,
    ).toBe(false);
  });
});

describe("Dataset Episode list wire contract", () => {
  it("accepts the formal nullable review projection for a newly ingested Episode", () => {
    const parsed = versionEpisodeListItemWireSchema.parse({
      scope: {
        organization_id: "organization-a",
        project_id: "project-a",
        region_code: "region-a",
      },
      dataset_id: "dataset_ingest_a",
      version_id: "version_lance_1",
      episode_id: "episode_ingest_a",
      selected_revision: {
        episode_id: "episode_ingest_a",
        revision_id: "revision_ingest_a",
        ordinal: 0,
        content_sha256: "a".repeat(64),
      },
      included: true,
      success_state: "SUCCEEDED",
      task: "task-a",
      robot_id: "robot-a",
      review_status: null,
      review_finding_count: null,
    });

    expect(parsed.review_status).toBeNull();
    expect(parsed.review_finding_count).toBeNull();
  });
});

describe("Dataset Episode revision-history wire contract", () => {
  const item = {
    scope: {
      organization_id: "organization-a",
      project_id: "project-a",
      region_code: "region-a",
    },
    dataset_id: "dataset_ingest_a",
    episode_id: "episode_ingest_a",
    version_id: "version_lance_2",
    display_version: "v2",
    version_kind: "CLEANED",
    version_status: "READY",
    version_created_at: "2026-08-24T10:00:00+00:00",
    version_published_at: "2026-08-24T10:10:00+00:00",
    selected_revision: {
      episode_id: "episode_ingest_a",
      revision_id: "revision_ingest_a",
      ordinal: 0,
      content_sha256: "b".repeat(64),
    },
  };

  it("accepts one immutable selected revision per visible Version", () => {
    const parsed = episodeRevisionHistoryWireSchema.parse({
      items: [item],
      page_info: {
        after: null,
        before: null,
        has_next: false,
        has_previous: false,
      },
      snapshot_at: "2026-08-24T10:20:00+00:00",
      snapshot_id: "history-snapshot-a",
      scope: item.scope,
      request_id: "request-history-a",
      contract_version: "dataset-version-review.v1alpha1",
    });

    expect(parsed.items[0]?.selected_revision.revision_id).toBe(
      "revision_ingest_a",
    );
  });

  it("rejects a history item whose selected revision belongs to another Episode", () => {
    expect(() =>
      episodeRevisionHistoryWireSchema.parse({
        items: [
          {
            ...item,
            selected_revision: {
              ...item.selected_revision,
              episode_id: "episode_other_a",
            },
          },
        ],
        page_info: {
          after: null,
          before: null,
          has_next: false,
          has_previous: false,
        },
        snapshot_at: "2026-08-24T10:20:00+00:00",
        snapshot_id: "history-snapshot-a",
        scope: item.scope,
        request_id: "request-history-a",
        contract_version: "dataset-version-review.v1alpha1",
      }),
    ).toThrow(/selected revision/u);
  });
});
