import { afterEach, describe, expect, it, vi } from "vitest";
import {
  datasetIds,
  datasetFixtureScope,
  episodePageFixture,
  episodeRevisionFixture,
  versionBootstrapFixture,
} from "../../../mocks/fixtures/datasets/core";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import type { DatasetId } from "../../../entities/dataset";
import type { DatasetVersionId } from "../../../entities/dataset-version";
import type { EpisodeId } from "../../../entities/episode";
import { resolveViewerEpisode } from "./queries";
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => {
  vi.resetAllMocks();
  useShellStore.setState({ scope: null });
});

describe("fixed version Episode viewer", () => {
  it("finds an Episode beyond the first 100 rows using the same snapshot", async () => {
    useShellStore
      .getState()
      .setScope({
        organizationId: datasetFixtureScope.organization_id,
        projectId: datasetFixtureScope.project_id,
        regionCode: datasetFixtureScope.region_code,
      });
    vi.mocked(request)
      .mockResolvedValueOnce(versionBootstrapFixture)
      .mockResolvedValueOnce({
        ...episodePageFixture,
        items: [],
        page_info: {
          after: "page-2",
          before: null,
          has_next: true,
          has_previous: false,
        },
      })
      .mockResolvedValueOnce(episodePageFixture)
      .mockResolvedValueOnce(episodeRevisionFixture);
    const result = await resolveViewerEpisode(
      datasetIds.dataset as DatasetId,
      datasetIds.reviewing as DatasetVersionId,
      episodePageFixture.items[0]!.episode_id as EpisodeId,
    );
    expect(result.episode.episodeId).toBe(
      episodePageFixture.items[0]!.episode_id,
    );
    expect(vi.mocked(request).mock.calls[2]![0].query).toMatchObject({
      after: "page-2",
      limit: 100,
      snapshotToken: versionBootstrapFixture.data.snapshot_token,
    });
  });
});
