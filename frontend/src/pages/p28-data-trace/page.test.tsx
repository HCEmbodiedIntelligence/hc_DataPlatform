// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { useShellStore } from "../../shared/scope/shell-store";
import { episodeDetailFixture } from "../p06-dataset-detail/testing/episode-detail-fixture";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { loadEpisodeAnnotationTracks } from "../../features/datasets/episodes/episode-annotations";
import { findOrigins, loadMembers, suggestNames } from "./api";
import { Component } from "./page";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  findOrigins: vi.fn(),
  suggestNames: vi.fn(),
  loadMembers: vi.fn(),
}));
vi.mock("../../features/datasets/episodes/episode-detail", () => ({
  loadEpisodeOverview: vi.fn(),
}));
vi.mock("../../features/datasets/episodes/episode-annotations", () => ({
  loadEpisodeAnnotationTracks: vi.fn(),
}));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => true }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  useShellStore.setState({ scope: null });
});

it("keeps the problem sample visible while comparing a peer's instruction, Tags and playback", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", (media: string) => ({
    media,
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  const { data, view, scope } = episodeDetailFixture();
  useShellStore.setState({ scope });
  const reference = {
    ...data,
    identity: {
      ...data.identity,
      dataset_id: "dataset_trace",
      episode_id: "episode_origin",
    },
  };
  const peer = {
    ...reference,
    data_version_id: "ev_peer",
    identity: { ...reference.identity, episode_id: "episode_peer" },
  };
  const batch = {
    batch_number: 11,
    channel: "web" as const,
    created_at: "2026-11-10T09:00:00Z",
  };
  vi.mocked(findOrigins).mockResolvedValue([
    {
      data: reference,
      identifier: reference.data_version_id,
      viewer_version_id: "version_trace",
      batches: [batch, { ...batch, batch_number: 12 }],
    },
  ]);
  vi.mocked(loadMembers).mockResolvedValue({
    total: 2,
    upload_count: 2,
    items: [reference, peer].map((sample, i) => ({
      data: sample,
      viewer_version_id: "version_trace",
      upload_id: "upload-" + i,
      collection_description: "料框抓取",
      upload_note: "透明件补采",
      raw_source_ids: [],
    })),
  });
  vi.mocked(loadEpisodeOverview).mockImplementation(async (_scope, sample) => ({
    ...view,
    data: sample,
    task: {
      ...view.task,
      instructions: [
        sample.data_version_id === "ev_peer" ? "取出透明件" : "取出样件",
      ],
    },
    annotation: {
      ...view.annotation,
      availability: "AVAILABLE",
      task_id: "test",
      current_revision: {
        task_id: "test",
        revision: 1,
        content_hash: "a".repeat(64),
        author_id: "reviewer",
        created_at: batch.created_at,
      },
    },
  }));
  vi.mocked(loadEpisodeAnnotationTracks).mockImplementation(
    async (_scope, info) => ({
      count: 1,
      tracks: [
        {
          id: "track",
          label: "Tag",
          segments: [
            {
              id: "tag",
              label: info.data.data_version_id === "ev_peer" ? "打滑" : "抓取",
              startNs: "0",
              endNs: "1",
              tone: "action",
            },
          ],
        },
      ],
    }),
  );
  render(
    <MemoryRouter initialEntries={["/data-trace?id=episode_origin&batch=11"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <Component />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("抓取")).toBeInTheDocument();
  expect(screen.getAllByText("透明件补采")).toHaveLength(2);
  expect(loadMembers).toHaveBeenCalledWith(scope, 11, 1, expect.anything());
  fireEvent.click(
    screen
      .getAllByRole("button", { name: "查看样本" })
      .find((button) => !(button as HTMLButtonElement).disabled)!,
  );
  expect(await screen.findByText("打滑")).toBeInTheDocument();
  expect(screen.getByText("抓取")).toBeInTheDocument();
  expect(screen.getByText("取出透明件")).toBeInTheDocument();
  expect(
    screen.getAllByRole("link", { name: "打开同步回放" })[1],
  ).toHaveAttribute(
    "href",
    expect.stringContaining("/episodes/episode_peer/view?returnTo="),
  );
});

it("searches a filename fragment and looks up the selected export", async () => {
  const { scope } = episodeDetailFixture();
  useShellStore.setState({ scope });
  const name = "teleop_20261117_a1b2c3.mcap";
  vi.mocked(suggestNames).mockResolvedValue([name]);
  vi.mocked(findOrigins).mockResolvedValue([]);
  render(
    <MemoryRouter initialEntries={["/data-trace"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <Component />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "1117_a1" } });
  expect((await screen.findAllByText(name)).length).toBeGreaterThan(0);
  expect(suggestNames).toHaveBeenCalledWith(
    scope,
    "1117_a1",
    expect.anything(),
  );
  fireEvent.click(screen.getByRole("button", { name: "查找上传批次" }));
  const result = await screen.findByRole("button", { name });
  fireEvent.click(result);
  await vi.waitFor(() =>
    expect(findOrigins).toHaveBeenCalledWith(scope, name, expect.anything()),
  );
});
