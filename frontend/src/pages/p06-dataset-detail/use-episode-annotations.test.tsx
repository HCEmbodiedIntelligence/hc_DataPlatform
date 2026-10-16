// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { type ReactNode } from "react";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { request } from "../../shared/api/http-client";
import { episodeDetailFixture } from "./testing/episode-detail-fixture";
import { useEpisodeAnnotations } from "./use-episode-annotations";

vi.mock("../../features/datasets/episodes/episode-detail", async (original) => ({
  ...(await original<typeof import("../../features/datasets/episodes/episode-detail")>()),
  loadEpisodeOverview: vi.fn(),
}));
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function setup() {
  const fixture = episodeDetailFixture();
  fixture.view.annotation = {
    ...fixture.view.annotation,
    availability: "AVAILABLE",
    task_id: "task-annotation",
    current_revision: {
      task_id: "task-annotation",
      revision: 2,
      content_hash: "c".repeat(64),
      author_id: "author",
      created_at: "2026-10-16T00:00:00Z",
    },
  };
  vi.mocked(loadEpisodeOverview).mockResolvedValue(fixture.view);
  const revision = {
    ...fixture.view.annotation.current_revision,
    data_ref: fixture.data,
    tag_schema_id: "schema",
    tag_schema_version: 1,
    tags: [
      {
        annotation_id: "tag-1",
        tag_id: "pick",
        path: ["pick"],
        start_step: 0,
        end_step: 1,
        relations: [],
      },
    ],
  };
  vi.mocked(request).mockImplementation(async ({ path }) =>
    path.endsWith("/revisions/2")
      ? revision
      : {
          schema_id: "schema",
          version: 1,
          project_id: fixture.data.identity.project_id,
          document: { nodes: [{ tag_id: "pick", display_name: "抓取" }] },
        },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const props = {
    scope: fixture.scope,
    data: fixture.data,
    canRead: true,
    canReadAnnotations: true,
    startNs: "1000000000",
    endNs: "2000000000",
  };
  return { fixture, revision, wrapper, props };
}

it("loads the exact saved revision and places its labels on the Episode clock", async () => {
  const { wrapper, props } = setup();
  const { result } = renderHook(() => useEpisodeAnnotations(props), { wrapper });
  await waitFor(() => expect(result.current.tagCount).toBe(1));
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({ path: "/annotation-tasks/task-annotation/revisions/2" }),
  );
  expect(result.current.tracks[0]?.segments[0]).toMatchObject({
    label: "抓取",
    startNs: "1000000000",
    endNs: "1033333333",
  });
});

it("rejects annotations belonging to another immutable Episode version", async () => {
  const { wrapper, props, revision } = setup();
  revision.data_ref = { ...revision.data_ref, data_version_id: "another-version" };
  const { result } = renderHook(() => useEpisodeAnnotations(props), { wrapper });
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(result.current.tracks).toEqual([]);
  expect(request).toHaveBeenCalledTimes(1);
});

it("does not fetch annotations without read permission and clears cached tracks when permission is revoked", async () => {
  const { wrapper, props } = setup();
  const { result, rerender } = renderHook(useEpisodeAnnotations, {
    wrapper,
    initialProps: { ...props, canReadAnnotations: false },
  });
  expect(loadEpisodeOverview).not.toHaveBeenCalled();
  expect(result.current.tracks).toEqual([]);
  rerender(props);
  await waitFor(() => expect(result.current.tagCount).toBe(1));
  rerender({ ...props, canReadAnnotations: false });
  expect(result.current.tracks).toEqual([]);
  expect(result.current.annotation).toBeUndefined();
});
