/** Temporary dependency seam. Replace only with T4's `routes.episodeViewer.build`. */
export interface EpisodeViewerLinkInput {
  readonly datasetId: string;
  readonly versionId: string;
  readonly episodeId: string;
  readonly t?: string;
  readonly startNs?: string;
  readonly endNs?: string;
}

export type EpisodeViewerLinkBuilder = (input: EpisodeViewerLinkInput) => string;

let episodeViewerLinkBuilder: EpisodeViewerLinkBuilder | null = null;

export function registerEpisodeViewerLinkBuilder(builder: EpisodeViewerLinkBuilder): () => void {
  episodeViewerLinkBuilder = builder;
  return () => {
    if (episodeViewerLinkBuilder === builder) episodeViewerLinkBuilder = null;
  };
}

export function buildPendingEpisodeViewerLink(input: EpisodeViewerLinkInput): string | null {
  return episodeViewerLinkBuilder?.(input) ?? null;
}

