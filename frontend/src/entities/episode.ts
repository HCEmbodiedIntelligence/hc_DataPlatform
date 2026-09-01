import type { DatasetId } from './dataset';
import type { DatasetVersionId } from './dataset-version';

const episodeIdPattern = /^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const episodeRevisionIdPattern = /^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const episodeStreamIdPattern = /^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;

declare const episodeIdBrand: unique symbol;
declare const episodeRevisionIdBrand: unique symbol;
declare const episodeStreamIdBrand: unique symbol;

export type EpisodeId = string & { readonly [episodeIdBrand]: 'EpisodeId' };
export type EpisodeRevisionId = string & {
  readonly [episodeRevisionIdBrand]: 'EpisodeRevisionId';
};
export type EpisodeStreamId = string & {
  readonly [episodeStreamIdBrand]: 'EpisodeStreamId';
};

export const EPISODE_SUCCESS_STATES = ['SUCCEEDED', 'FAILED', 'UNKNOWN'] as const;
export type EpisodeSuccessState = (typeof EPISODE_SUCCESS_STATES)[number];

export const EPISODE_REVIEW_STATUSES = [
  'UNREVIEWED',
  'ACCEPTED',
  'HAS_FINDING',
  'UNKNOWN',
] as const;
export type EpisodeReviewStatus = (typeof EPISODE_REVIEW_STATUSES)[number];

export type EpisodeStream = Readonly<{
  id: EpisodeStreamId;
  channelPath: string;
  kind: string;
  startNs: string;
  endNs: string;
}>;

export type EpisodeRevision = Readonly<{
  id: EpisodeRevisionId;
  episodeId: EpisodeId;
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  ordinal: string;
  contentSha256: string;
  startedAtNs: string;
  durationNs: string;
  streams: readonly EpisodeStream[];
}>;

export type Episode = Readonly<{
  id: EpisodeId;
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  selectedRevisionId: EpisodeRevisionId;
  successState: EpisodeSuccessState;
  reviewStatus: EpisodeReviewStatus;
  included: boolean;
}>;

export function isEpisodeId(value: unknown): value is EpisodeId {
  return typeof value === 'string' && episodeIdPattern.test(value);
}

export function isEpisodeRevisionId(value: unknown): value is EpisodeRevisionId {
  return typeof value === 'string' && episodeRevisionIdPattern.test(value);
}

export const isRevisionId = isEpisodeRevisionId;

export function isEpisodeStreamId(value: unknown): value is EpisodeStreamId {
  return typeof value === 'string' && episodeStreamIdPattern.test(value);
}

export function isEpisodeSuccessState(value: unknown): value is EpisodeSuccessState {
  return typeof value === 'string' && EPISODE_SUCCESS_STATES.includes(value as EpisodeSuccessState);
}

export function isEpisodeReviewStatus(value: unknown): value is EpisodeReviewStatus {
  return (
    typeof value === 'string' && EPISODE_REVIEW_STATUSES.includes(value as EpisodeReviewStatus)
  );
}

export function isEpisode(value: unknown): value is Episode {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<Episode>;
  return (
    isEpisodeId(candidate.id) &&
    typeof candidate.datasetId === 'string' &&
    typeof candidate.versionId === 'string' &&
    isEpisodeRevisionId(candidate.selectedRevisionId) &&
    isEpisodeSuccessState(candidate.successState) &&
    isEpisodeReviewStatus(candidate.reviewStatus) &&
    typeof candidate.included === 'boolean'
  );
}
