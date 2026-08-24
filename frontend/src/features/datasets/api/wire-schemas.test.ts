import { describe, expect, it } from "vitest";
import {
  episodeRevisionHistoryWireSchema,
  versionEpisodeListItemWireSchema,
} from "./wire-schemas";

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
